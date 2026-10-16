"""Supervised orchestrator for durable tool-free conversation and run lifecycle.

Enforces:
- 8 outer orchestration turns maximum
- 600-second run deadline shared across turns
- per-request timeout capped by remaining run time
- safe terminal error recording
- asynchronous task cancellation without late state overwrite
- conceptual answers invoking zero tools
- bound portfolio revision context (or None if no portfolio exists)
"""

import asyncio
import inspect
import json
import logging
import re
from typing import Any, Literal, cast

from openai import OpenAIError

from .allocation import DiscoveryProvider
from .config import Settings
from .context import CHARACTER_LIMIT, READ_CAPABILITIES, RunContext, read_tools
from .conversation_engine import ConversationDeps, execute_pydantic_ai_run
from .evaluation_budget import create_task_budget
from .failures import describe_failure
from .financial_data import FinancialProvider
from .financial_tools import FinancialTools
from .model_view import fit
from .persistence import Database, DecimalEncoder, IncompatibleStateError
from .pipeline import InvalidReview, bounded_effort
from .proposal_tools import ProposalTools
from .providers import DataProvider, ModelIncomplete, ModelTurn, SuppliedDataProvider, ToolCall
from .research import ResearchProvider
from .research_tools import ResearchTools
from .schemas import RunRecord, ToolOutcome
from .web_search import HOSTED_TOOL, NO_LOOKUP, WebSearchTools
from .workflow_tools import WorkflowTools

logger = logging.getLogger(__name__)

MAX_TURNS = 8
ANSWER_RETRY = ("Your last reply was empty or was a tool call written as text. Reply to the user in plain language "
                "using the results above. To use a tool, call it through the tool interface instead.")
TOOL_CALL_KEYS = {"name", "arguments", "type", "id", "call_id", "tool", "tool_calls", "function", "parameters",
                  "args", "input", "retrieval"}
MAX_DEADLINE_SECONDS = 600.0


def valid_answer(text: str | None) -> bool:
    """False for an empty reply or one that is only tool-call-shaped JSON; prose, code and other JSON pass."""
    body = (text or "").strip()
    if body.startswith("```") and body.endswith("```"):
        body = body.strip("`").removeprefix("json").strip()
    if not body:
        return False
    try:
        parsed = json.loads(body)
    except ValueError:
        return True
    items = parsed if isinstance(parsed, list) else [parsed]
    return not (items and all(
        isinstance(item, dict) and item.keys() <= TOOL_CALL_KEYS
        and bool(item.keys() & {"name", "tool", "tool_calls", "function", "retrieval"}) for item in items))


class Orchestrator:
    """Supervises in-process asynchronous execution of thread runs."""

    def __init__(
        self,
        db: Database,
        transport: Any,
        settings: Settings,
        financial: FinancialProvider | None = None,
        research: ResearchProvider | None = None,
        discovery: DiscoveryProvider | None = None,
        data: DataProvider | None = None,
        engine: Literal["pydantic_ai", "legacy"] | None = None,
    ) -> None:
        self.db = db
        self.transport = transport
        self.settings = settings
        self.financial = financial
        self.research = research
        self.discovery = discovery
        self.data = data or SuppliedDataProvider()
        self.engine = engine
        self._active_tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def has_active_runs(self) -> bool:
        return any(not task.done() for task in self._active_tasks.values())

    def submit_supervised_run(
        self,
        thread_id: str,
        run_record: RunRecord,
        user_text: str,
        workflow_input: dict[str, Any] | None = None,
        hypothetical_artifact_id: str | None = None,
    ) -> None:
        """Launch background supervised task for an accepted run."""
        task = asyncio.create_task(
            self._execute_run(
                thread_id=thread_id,
                run_record=run_record,
                user_text=user_text,
                workflow_input=workflow_input,
                hypothetical_artifact_id=hypothetical_artifact_id,
            )
        )
        self._active_tasks[run_record.id] = task

        def _cleanup(t: asyncio.Task[None]) -> None:
            self._active_tasks.pop(run_record.id, None)

        task.add_done_callback(_cleanup)

    async def cancel_run(self, thread_id: str, run_id: str) -> RunRecord | None:
        """Mark the run cancelled in the database, then stop its in-process task.

        The database decides first: a run that is not in this thread, or that already ended some
        other way, keeps its task untouched.
        """
        record = self.db.cancel_run(thread_id, run_id)
        task = self._active_tasks.get(run_id)
        if record is not None and record.status == "cancelled" and task and not task.done():
            task.cancel()
        return record

    def _resolve_engine(self) -> str:
        """Resolve conversation engine selected before run execution starts."""
        if self.engine is not None:
            return self.engine
        import os
        return os.environ.get("TFS_ORCHESTRATOR_ENGINE", "pydantic_ai").lower()

    async def _execute_run(
        self,
        thread_id: str,
        run_record: RunRecord,
        user_text: str,
        workflow_input: dict[str, Any] | None,
        hypothetical_artifact_id: str | None,
    ) -> None:
        """Execute run through selected engine. Selected before start; never switched mid-run."""
        engine = self._resolve_engine()
        try:
            self.db.validate_execution_state(run_record.id, expected_engine=engine)
        except IncompatibleStateError as exc:
            self._record_terminal_failure(run_record.id, str(exc))
            return

        if engine == "legacy":
            await self._execute_legacy_run(
                thread_id=thread_id,
                run_record=run_record,
                user_text=user_text,
                workflow_input=workflow_input,
                hypothetical_artifact_id=hypothetical_artifact_id,
            )
            return

        await self._execute_pydantic_ai_run(
            thread_id=thread_id,
            run_record=run_record,
            user_text=user_text,
            workflow_input=workflow_input,
            hypothetical_artifact_id=hypothetical_artifact_id,
        )

    async def _execute_pydantic_ai_run(
        self,
        thread_id: str,
        run_record: RunRecord,
        user_text: str,
        workflow_input: dict[str, Any] | None,
        hypothetical_artifact_id: str | None,
    ) -> None:
        run_id = run_record.id
        start_time = asyncio.get_event_loop().time()
        deadline = start_time + MAX_DEADLINE_SECONDS
        ledger = create_task_budget(self.settings, max_attempts=15, deadline_seconds=MAX_DEADLINE_SECONDS)

        try:
            context = RunContext(self.db, run_record)
            financial_tools = FinancialTools(self.financial)
            research_tools = ResearchTools(financial_tools, self.research, workflow_input, self.discovery)
            web = WebSearchTools(
                lambda search_messages, timeout: self._call_transport(
                    search_messages, timeout, [HOSTED_TOOL], tool_choice="required"),
                self.settings,
            )
            workflow_tools = WorkflowTools(financial_tools, research_tools, self.data, self.transport,
                                           self.settings, deadline, user_text, workflow_input)
            proposal_tools = ProposalTools(financial_tools)
            capabilities = {**READ_CAPABILITIES, **financial_tools.capabilities,
                            **research_tools.capabilities, **workflow_tools.capabilities,
                            **web.capabilities, **proposal_tools.capabilities}
            initial_messages = context.messages(comparison=research_tools.comparison)

            deps = ConversationDeps(
                db=self.db,
                thread_id=thread_id,
                run_record=run_record,
                context=context,
                ledger=ledger,
                settings=self.settings,
                transport=self.transport,
                financial_tools=financial_tools,
                research_tools=research_tools,
                workflow_tools=workflow_tools,
                proposal_tools=proposal_tools,
                web=web,
                capabilities=capabilities,
                user_text=user_text,
                is_stopped=lambda: self._stopped(thread_id, run_id),
                record_terminal_failure=lambda msg: self._record_terminal_failure(run_id, msg),
                initial_messages=initial_messages,
            )

            await execute_pydantic_ai_run(deps)
        except asyncio.CancelledError:
            logger.info("Supervised task for run %s was cancelled.", run_id)
        except Exception as exc:
            logger.error("Orchestration error in run %s (%s).", run_id, type(exc).__name__, exc_info=True)
            failure = describe_failure(exc)
            self._record_terminal_failure(run_id, "Internal orchestration error." if failure.kind == "internal"
                                          else failure.message)

    async def _execute_legacy_run(
        self,
        thread_id: str,
        run_record: RunRecord,
        user_text: str,
        workflow_input: dict[str, Any] | None,
        hypothetical_artifact_id: str | None,
    ) -> None:
        run_id = run_record.id
        start_time = asyncio.get_event_loop().time()
        deadline = start_time + MAX_DEADLINE_SECONDS

        try:
            context = RunContext(self.db, run_record)
            financial_tools = FinancialTools(self.financial)
            research_tools = ResearchTools(financial_tools, self.research, workflow_input, self.discovery)
            web = WebSearchTools(
                lambda search_messages, timeout: self._call_transport(
                    search_messages, timeout, [HOSTED_TOOL], tool_choice="required"),
                self.settings,
            )
            workflow_tools = WorkflowTools(financial_tools, research_tools, self.data, self.transport,
                                           self.settings, deadline, user_text, workflow_input)
            capabilities = {**READ_CAPABILITIES, **financial_tools.capabilities,
                            **research_tools.capabilities, **workflow_tools.capabilities,
                            **web.capabilities, **ProposalTools(financial_tools).capabilities}
            messages = context.messages(comparison=research_tools.comparison)
            initial_messages = messages
            exchanges: list[list[dict[str, Any]]] = []
            seen_calls: set[str] = set()
            # Calls whose result was an error or had to be cut to fit: repeating one returns the same.
            limited_calls: set[tuple[str, str]] = set()
            # Workflows that failed and were not later completed; the answer always says so.
            failed_workflows: dict[str, str] = {}
            answer_retried = False
            form = workflow_tools.inputs.workflow
            form_answer: str | None = None

            turns_count = 0
            while turns_count < MAX_TURNS:
                turns_count += 1
                now = asyncio.get_event_loop().time()
                remaining_deadline = deadline - now
                if remaining_deadline <= 0:
                    self._record_terminal_failure(run_id, "Run deadline of 600 seconds exceeded.")
                    return

                request_timeout = min(self.settings.request_timeout, remaining_deadline)

                # Call model transport
                try:
                    if form is not None:
                        # A form submission: its validated inputs run the selected workflow, and that workflow's
                        # recommendation is the answer. No model turn reinterprets the form or restates the result.
                        turn = ModelTurn(text=form_answer) if form_answer is not None else ModelTurn(
                            calls=[ToolCall("form", form, "{}")])
                    else:
                        turn = await self._call_transport(
                            messages=messages,
                            timeout=request_timeout,
                            # Every capability stays callable; only schemas this run cannot use yet are left out.
                            tools=read_tools({name: capability for name, capability in capabilities.items()
                                              if name not in research_tools.unadvertised(user_text)}),
                        )
                except asyncio.CancelledError:
                    # Cancelled while waiting on transport
                    logger.info("Run %s was cancelled during model transport.", run_id)
                    return
                except TimeoutError:
                    self._record_terminal_failure(run_id, f"Model request timed out after {request_timeout:.1f}s.")
                    return
                except Exception as exc:
                    # Token counts and provider detail stay in the log; the run gets the safe message.
                    logger.error("Model transport error during run %s: %s", run_id, exc, exc_info=True)
                    self._record_terminal_failure(run_id, describe_failure(exc).message)
                    return
                if self._stopped(thread_id, run_id):
                    return

                if turn.calls:
                    if (len(turn.calls) != 1 or turn.text or turn.answer is not None
                            or any(item.get("type") not in {"reasoning", "function_call"}
                                   for item in turn.continuation)):
                        self._record_terminal_failure(run_id, "Invalid mixed or parallel model response.")
                        return
                    call = turn.calls[0]
                    if call.name not in capabilities:
                        self._record_terminal_failure(run_id, "Unexpected tool call.")
                        return
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", call.call_id) or call.call_id in seen_calls:
                        self._record_terminal_failure(run_id, "Invalid or repeated application call ID.")
                        return
                    continuation_calls = [item for item in turn.continuation if item.get("type") == "function_call"]
                    if continuation_calls and (len(continuation_calls) != 1 or any(
                        continuation_calls[0].get(key) != value for key, value in {
                            "call_id": call.call_id, "name": call.name, "arguments": call.arguments,
                        }.items()
                    )):
                        self._record_terminal_failure(run_id, "Invalid application continuation.")
                        return
                    try:
                        if len(call.arguments) > CHARACTER_LIMIT:
                            raise ValueError("Arguments exceed budget.")
                        capability = capabilities[call.name]
                        parsed = capability.arguments.model_validate_json(call.arguments)
                    except ValueError:
                        self._record_terminal_failure(run_id, "Invalid application tool arguments.")
                        return
                    seen_calls.add(call.call_id)
                    signature = (call.name, json.dumps(parsed.model_dump(mode="json"), sort_keys=True))
                    if signature in limited_calls:
                        self._record_terminal_failure(
                            run_id, "The model repeated a retrieval that already failed or was cut to fit the "
                                    "context budget. Ask a narrower question.")
                        return

                if not turn.calls and not valid_answer(turn.text):
                    # An empty reply, or a tool call written as text, is never stored as the answer. One retry
                    # resends the same context, so no completed tool or proposal step runs again.
                    if answer_retried:
                        self._record_terminal_failure(run_id, describe_failure(
                            InvalidReview("Empty or tool-call-shaped final answer.")).message)
                        return
                    answer_retried = True
                    logger.warning("Invalid final answer in run %s; retrying once.", run_id)
                    messages = [*messages, {"role": "system", "content": ANSWER_RETRY}]
                    continue

                # Continuation state is server-only. A tool-call turn is stored after its handler,
                # so a rejected search query never reaches storage.
                if turn.continuation and not turn.calls:
                    self.db.store_private_continuation(run_id, turn.continuation)

                # Conceptual / tool-free answer path: model produced text response
                if turn.text or not turn.calls:
                    assistant_text = turn.text or ""
                    for name, error in failed_workflows.items():
                        label = name.removeprefix("run_").replace("_", " ").capitalize()
                        assistant_text = f"{assistant_text}\n\n{label} did not complete: {error}".strip()
                    if web.unavailable and not web.completed:
                        # Never let prose imply a current lookup that did not occur.
                        assistant_text = (
                            f"{assistant_text}\n\nHosted web search was unavailable. {NO_LOOKUP}".strip())
                    citations = [*(turn.citations or []), *web.citations]
                    asst_msg = self.db.add_message(
                        thread_id=thread_id,
                        role="assistant",
                        text=assistant_text,
                        run_id=run_id,
                        citations=citations,
                        # Keeps cited web research readable by later runs once its outcome leaves context.
                        artifact_ids=list(dict.fromkeys(c["artifact_id"] for c in web.citations)),
                    )
                    # Complete run
                    self.db.update_run(
                        run_id=run_id,
                        status="completed",
                        additional_message_ids=[asst_msg.id],
                    )
                    return

                call = turn.calls[0]
                tool_remaining = max(0, deadline - asyncio.get_event_loop().time())
                known_artifacts = set(context.artifact_ids)
                try:
                    pending_result = capability.handler(context, parsed)
                    if inspect.isawaitable(pending_result):
                        result = await asyncio.wait_for(pending_result, timeout=min(
                            MAX_DEADLINE_SECONDS if capability.effect == "workflow" else self.settings.request_timeout,
                            tool_remaining,
                        ))
                    else:
                        result = pending_result
                except TimeoutError:
                    if (capability.effect == "workflow" and tool_remaining <= self.settings.request_timeout
                            or asyncio.get_event_loop().time() >= deadline):
                        self._record_terminal_failure(run_id, "Run deadline of 600 seconds exceeded.")
                        return
                    result = (web.unavailable_result(context, "deadline", None) if call.name in web.capabilities
                              else {"error": "Financial request timed out; no result is available."})
                except (InvalidReview, OpenAIError, ValueError) as exc:
                    # Nested model failures (ModelIncomplete is also a ValueError) keep their real category, so the
                    # model can explain them instead of reporting missing data.
                    failure = describe_failure(exc)
                    logger.warning("Tool %s failed in run %s (%s): %s", call.name, run_id, failure.kind, exc)
                    result = {"error": failure.message, "retryable": failure.retryable}
                    limited_calls.add(signature)
                if capability.effect == "workflow":
                    if "error" in result:
                        failed_workflows[call.name] = str(result["error"])
                    else:
                        failed_workflows.pop(call.name, None)
                if self._stopped(thread_id, run_id):
                    return
                artifact_ids = list(result.get("artifact_ids", []))
                proposal_ids = list(result.get("proposal_ids", []))
                for key in ("reference", "before_artifact", "after_artifact"):
                    if isinstance(result.get(key), dict):
                        artifact_ids.append(result[key]["id"])
                # A step links only the artifacts it created; reading a known artifact adds no second result card.
                artifact_ids = [ref for ref in dict.fromkeys(artifact_ids) if ref not in known_artifacts]
                rejected_query = (isinstance(result.get("payload"), dict)
                                  and result["payload"].get("reason") == "rejected_query")
                if turn.continuation and not rejected_query:
                    self.db.store_private_continuation(run_id, turn.continuation)
                output = json.dumps(result, cls=DecimalEncoder)
                exchange = list(turn.continuation)
                if not continuation_calls:
                    exchange.append({
                        "type": "function_call", "call_id": call.call_id,
                        "name": call.name, "arguments": call.arguments,
                    })
                output_item = {"type": "function_call_output", "call_id": call.call_id, "output": output}
                required_size = len(initial_messages[-1]["content"]) + sum(len(json.dumps(item)) for item in exchange)
                if required_size + len(json.dumps(output_item)) > CHARACTER_LIMIT:
                    # The limit covers the whole serialized output item, so measure the doubly encoded result.
                    available = CHARACTER_LIMIT - required_size - len(json.dumps({**output_item, "output": ""}))
                    fitted = fit(result, available, lambda value: len(json.dumps(json.dumps(value, cls=DecimalEncoder))))
                    limited_calls.add(signature)
                    if fitted is None:
                        result = {"error": "Result exceeds the context budget even when summarized.",
                                  "artifact_ids": artifact_ids, "proposal_ids": proposal_ids}
                        if call.name == "get_artifact":
                            result["artifact_id"] = parsed.model_dump()["artifact_id"]
                        fitted = result
                    output_item["output"] = json.dumps(fitted, cls=DecimalEncoder)
                exchange.append(output_item)
                exchanges.append(exchange)
                messages = context.with_exchanges(initial_messages, exchanges)
                outcome = ToolOutcome(id=call.call_id, name=call.name,
                                      status="error" if "error" in result else "success",
                                      error=result.get("error"), artifact_ids=artifact_ids)
                outcome_message = self.db.add_message(
                    thread_id, "assistant", json.dumps({"retrieval": {
                        # A rejected search query may hold private details: never persist it.
                        "tool": call.name, "arguments": {} if rejected_query else parsed.model_dump(),
                    }}, cls=DecimalEncoder), run_id=run_id,
                    tool_outcomes=[outcome], artifact_ids=artifact_ids,
                    clarification_required=result.get("clarification_required"),
                    proposal_ids=proposal_ids,
                )
                self.db.update_run(run_id, "running", additional_message_ids=[outcome_message.id],
                                   additional_artifact_ids=artifact_ids)
                if form is not None:
                    if "error" in result:
                        # The workflow's safe failure message ends the run; the form can be submitted again.
                        self._record_terminal_failure(run_id, str(result["error"]))
                        return
                    clarification = result.get("clarification_required")
                    form_answer = str(clarification["message"]) if clarification else workflow_tools.answer or ""

            # Exceeded MAX_TURNS
            self._record_terminal_failure(
                run_id,
                f"Maximum orchestration turns ({MAX_TURNS}) reached without terminal completion.",
            )

        except asyncio.CancelledError:
            logger.info("Supervised task for run %s was cancelled.", run_id)
            # cancel_run recorded the cancellation in the database before cancelling this task
        except Exception as exc:
            logger.error("Orchestration error in run %s (%s).", run_id, type(exc).__name__, exc_info=True)
            failure = describe_failure(exc)
            self._record_terminal_failure(run_id, "Internal orchestration error." if failure.kind == "internal"
                                          else failure.message)

    async def _call_transport(
        self,
        messages: list[dict[str, Any]],
        timeout: float,
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
    ) -> ModelTurn:
        """Call underlying transport, respecting timeout.

        Every chat turn may select a tool, so its effort is capped at medium as in the pipeline. A response that ran
        out of output (the token cap, or no reason given) is retried once at lower effort; a successful tool call is
        never repeated because the retry happens before any call is executed.
        """
        sig = inspect.signature(self.transport.respond)
        kwargs: dict[str, Any] = {}
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        effort = bounded_effort(self.settings.reasoning_effort, "medium")
        for name, value in {"timeout": timeout, "tools": tools, "tool_choice": tool_choice,
                            "reasoning_effort": effort}.items():
            if name in sig.parameters or accepts_kwargs:
                kwargs[name] = value

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        try:
            return cast(ModelTurn, await asyncio.wait_for(self.transport.respond(messages, **kwargs), timeout=timeout))
        except ModelIncomplete as stopped:
            lower = bounded_effort(effort, "low")
            if "reasoning_effort" not in kwargs or lower == effort or stopped.reason not in {None, "max_output_tokens"}:
                raise
            logger.warning("Incomplete chat response at reasoning effort %s; retrying once at %s.", effort, lower)
        kwargs["reasoning_effort"] = lower
        remaining = max(0.0, deadline - loop.time())
        if "timeout" in kwargs:
            kwargs["timeout"] = remaining
        return cast(ModelTurn, await asyncio.wait_for(self.transport.respond(messages, **kwargs), timeout=remaining))

    def _stopped(self, thread_id: str, run_id: str) -> bool:
        """Whether the run ended while provider work was still returning; its late output is then dropped.

        Checked after every await and before the next write, with no await in between, so a stop
        cannot land between the check and the write.
        ponytail: holds for the single-process deployment only; a second process needs the check
        inside the write's transaction.
        """
        current = self.db.get_run(thread_id, run_id)
        return current is None or current.status != "running"

    def _record_terminal_failure(self, run_id: str, error_message: str) -> None:
        """Safely record terminal failure without overwriting cancelled state."""
        self.db.update_run(
            run_id=run_id,
            status="failed",
            error=error_message,
        )
