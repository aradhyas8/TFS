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
from typing import Any, cast

from .allocation import DiscoveryProvider
from .config import Settings
from .context import CHARACTER_LIMIT, READ_CAPABILITIES, RunContext, read_tools
from .financial_data import FinancialProvider
from .financial_tools import FinancialTools
from .persistence import Database, DecimalEncoder
from .proposal_tools import ProposalTools
from .providers import DataProvider, ModelTurn, SuppliedDataProvider
from .research import ResearchProvider
from .research_tools import ResearchTools
from .schemas import RunRecord, ToolOutcome
from .web_search import HOSTED_TOOL, NO_LOOKUP, WebSearchTools
from .workflow_tools import WorkflowTools

logger = logging.getLogger(__name__)

MAX_TURNS = 8
MAX_DEADLINE_SECONDS = 600.0


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
    ) -> None:
        self.db = db
        self.transport = transport
        self.settings = settings
        self.financial = financial
        self.research = research
        self.discovery = discovery
        self.data = data or SuppliedDataProvider()
        self._active_tasks: dict[str, asyncio.Task[None]] = {}

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
        """Cancel active in-process task and mark run cancelled in database."""
        task = self._active_tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        return self.db.cancel_run(thread_id, run_id)

    async def _execute_run(
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
            messages = context.messages(comparison=research_tools.comparison, workflow=workflow_tools.inputs.workflow)
            initial_messages = messages
            exchanges: list[list[dict[str, Any]]] = []
            seen_calls: set[str] = set()

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
                    turn = await self._call_transport(
                        messages=messages,
                        timeout=request_timeout,
                        tools=read_tools(capabilities),
                    )
                except asyncio.CancelledError:
                    # Cancelled while waiting on transport
                    logger.info("Run %s was cancelled during model transport.", run_id)
                    return
                except TimeoutError:
                    self._record_terminal_failure(run_id, f"Model request timed out after {request_timeout:.1f}s.")
                    return
                except Exception as exc:
                    logger.error("Model transport error during run %s: %s", run_id, exc, exc_info=True)
                    self._record_terminal_failure(run_id, f"Model service error: {exc}")
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

                # Continuation state is server-only. A tool-call turn is stored after its handler,
                # so a rejected search query never reaches storage.
                if turn.continuation and not turn.calls:
                    self.db.store_private_continuation(run_id, turn.continuation)

                # Conceptual / tool-free answer path: model produced text response
                if turn.text or not turn.calls:
                    assistant_text = turn.text or ""
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
                except ValueError:
                    result = {"error": "Requested data is unavailable to this run."}
                artifact_ids = list(result.get("artifact_ids", []))
                proposal_ids = list(result.get("proposal_ids", []))
                for key in ("reference", "before_artifact", "after_artifact"):
                    if isinstance(result.get(key), dict):
                        artifact_ids.append(result[key]["id"])
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
                    result = {"error": "Result exceeds retrieval budget. Request a smaller slice.",
                              "artifact_ids": artifact_ids, "proposal_ids": proposal_ids}
                    if call.name == "get_artifact":
                        result["artifact_id"] = parsed.model_dump()["artifact_id"]
                    output_item["output"] = json.dumps(result)
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

            # Exceeded MAX_TURNS
            self._record_terminal_failure(
                run_id,
                f"Maximum orchestration turns ({MAX_TURNS}) reached without terminal completion.",
            )

        except asyncio.CancelledError:
            logger.info("Supervised task for run %s was cancelled.", run_id)
            # Database cancel was already handled or will be handled by cancel_run
        except Exception as exc:
            logger.error("Orchestration error in run %s (%s).", run_id, type(exc).__name__)
            self._record_terminal_failure(run_id, "Internal orchestration error.")

    async def _call_transport(
        self,
        messages: list[dict[str, Any]],
        timeout: float,
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
    ) -> ModelTurn:
        """Call underlying transport, respecting timeout."""
        sig = inspect.signature(self.transport.respond)
        kwargs: dict[str, Any] = {}
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        for name, value in {"timeout": timeout, "tools": tools, "tool_choice": tool_choice}.items():
            if name in sig.parameters or accepts_kwargs:
                kwargs[name] = value

        coro = self.transport.respond(messages, **kwargs)
        return cast(ModelTurn, await asyncio.wait_for(coro, timeout=timeout))

    def _record_terminal_failure(self, run_id: str, error_message: str) -> None:
        """Safely record terminal failure without overwriting cancelled state."""
        self.db.update_run(
            run_id=run_id,
            status="failed",
            error=error_message,
        )
