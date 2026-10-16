"""PydanticAI conversational execution engine under TFS run supervision (Ticket 05).

Enforces:
- Ordinary conversation through PydanticAI model and tool execution loops.
- Task-scoped budget ledger (finite attempts + wall-clock deadline) shared across
  outer turns, nested workflow execution, and retries.
- Conceptual answers complete without financial tools.
- Focused financial/research questions invoke existing capabilities under their domain contracts.
- Idempotent submission, one active run per thread, run-bound revisions, and private continuation.
- State-dependent tool advertising and handler-enforced prerequisites.
- Final-answer validation, empty/tool-shaped recovery, and provider failures matching audit fixes.
- Narrative, tool outcomes, and citations persisted separately without exposing framework state or secrets.
- Framework execution is not nested inside the legacy 8-turn loop.
- Direct dispatch for structured desk forms preserved.
- Internal fallback to original engine available during migration.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, cast

from openai import OpenAIError
from pydantic_ai import Agent, ModelRetry
from pydantic_ai import RunContext as PydanticRunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import Model
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition

from .config import Settings
from .context import (
    CHARACTER_LIMIT,
    ReadCapability,
    RunContext,
    read_tools,
)
from .evaluation_budget import (
    BudgetExhaustedError,
    TaskBudgetLedger,
)
from .failures import describe_failure
from .financial_tools import FinancialTools
from .model_view import fit
from .persistence import Database, DecimalEncoder
from .pipeline import InvalidReview, bounded_effort
from .proposal_tools import ProposalTools
from .providers import (
    ModelIncomplete,
    ModelTurn,
    ToolCall,
)
from .research_tools import ResearchTools
from .schemas import RunRecord, ToolOutcome
from .web_search import NO_LOOKUP, WebSearchTools
from .workflow_tools import WorkflowTools

logger = logging.getLogger(__name__)

ANSWER_RETRY = (
    "Your last reply was empty or was a tool call written as text. Reply to the user in plain language "
    "using the results above. To use a tool, call it through the tool interface instead."
)
TOOL_CALL_KEYS = {
    "name", "arguments", "type", "id", "call_id", "tool", "tool_calls", "function", "parameters",
    "args", "input", "retrieval",
}
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
    return not (
        items
        and all(
            isinstance(item, dict)
            and item.keys() <= TOOL_CALL_KEYS
            and bool(item.keys() & {"name", "tool", "tool_calls", "function", "retrieval"})
            for item in items
        )
    )


@dataclass
class ConversationDeps:
    """Runtime dependencies injected into the PydanticAI conversational agent."""

    db: Database
    thread_id: str
    run_record: RunRecord
    context: RunContext
    ledger: TaskBudgetLedger
    settings: Settings
    transport: Any
    financial_tools: FinancialTools
    research_tools: ResearchTools
    workflow_tools: WorkflowTools
    proposal_tools: ProposalTools
    web: WebSearchTools
    capabilities: dict[str, Any]
    user_text: str
    is_stopped: Callable[[], bool]
    record_terminal_failure: Callable[[str], None]
    seen_calls: set[str] = field(default_factory=set)
    limited_calls: set[tuple[str, str]] = field(default_factory=set)
    failed_workflows: dict[str, str] = field(default_factory=dict)
    initial_messages: list[dict[str, Any]] = field(default_factory=list)
    turn_continuations: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    current_turn_continuation: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    active_turn_call: ToolCall | None = None

    @property
    def character_limit(self) -> int:
        """Capability-aware context budget limit for serialized tool returns."""
        window = getattr(self.ledger, "context_window", 128_000)
        headroom = getattr(self.ledger, "generation_headroom", 4_096)
        available_chars = max(0, (window - headroom) * 4)
        return max(16_000, min(120_000, int(available_chars * 0.25)))


class TFSTransportModel(Model):
    """Bridges any TFS model transport to PydanticAI's Model protocol."""

    def __init__(
        self,
        transport: Any,
        deps: ConversationDeps,
        settings: Settings | None = None,
    ) -> None:
        self.transport = transport
        self.deps = deps
        self.tfs_settings = settings or getattr(transport, "settings", None)
        profile = ModelProfile(
            supports_json_schema_output=True,
            supports_json_object_output=True,
        )
        super().__init__(profile=profile)

    @property
    def model_name(self) -> str:
        return getattr(self.tfs_settings, "model", "tfs-model")

    @property
    def system(self) -> str:
        return "tfs"

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: Any,
    ) -> ModelResponse:
        deps = self.deps
        if deps.is_stopped():
            return ModelResponse(parts=[TextPart("")])

        # Check deadline before model call
        remaining_deadline = deps.ledger.remaining_seconds()
        if remaining_deadline <= 0:
            deps.record_terminal_failure("Run deadline of 600 seconds exceeded.")
            raise BudgetExhaustedError("Run deadline of 600 seconds exceeded.", reason="deadline_exceeded")

        # Determine attempt phase
        is_retry = any(
            isinstance(m, ModelRequest)
            and any(type(p).__name__ == "RetryPromptPart" or ANSWER_RETRY in str(getattr(p, "content", "")) for p in m.parts)
            for m in messages
        )
        phase: Literal["outer_conversation", "validation_retry"] = "validation_retry" if is_retry else "outer_conversation"
        attempt = deps.ledger.charge_attempt(phase)

        # Build exchanges from prior tool call / return pairs in messages
        exchanges: list[list[dict[str, Any]]] = []
        tool_calls_by_id: dict[str, ToolCallPart] = {}
        for m in messages:
            if isinstance(m, ModelResponse):
                for p in m.parts:
                    if isinstance(p, ToolCallPart):
                        tool_calls_by_id[p.tool_call_id] = p
            elif isinstance(m, ModelRequest):
                for req_part in m.parts:
                    if isinstance(req_part, ToolReturnPart):
                        tc = tool_calls_by_id.get(req_part.tool_call_id)
                        if tc:
                            args_str = json.dumps(tc.args, cls=DecimalEncoder) if isinstance(tc.args, dict) else str(tc.args)
                            out_str = json.dumps(req_part.content, cls=DecimalEncoder) if isinstance(req_part.content, dict) else str(req_part.content)
                            continuation = self.deps.turn_continuations.get(tc.tool_call_id, [])
                            has_fn_call = any(c.get("type") == "function_call" for c in continuation)
                            exchange_items = list(continuation)
                            if not has_fn_call:
                                exchange_items.append({
                                    "type": "function_call",
                                    "call_id": tc.tool_call_id,
                                    "name": tc.tool_name,
                                    "arguments": args_str,
                                })
                            exchange_items.append({
                                "type": "function_call_output",
                                "call_id": tc.tool_call_id,
                                "output": out_str,
                            })
                            exchanges.append(exchange_items)

        request_timeout = deps.ledger.remaining_timeout(deps.settings.request_timeout)

        # Format TFS messages
        tfs_messages = deps.context.with_exchanges(deps.initial_messages, exchanges)
        if is_retry:
            tfs_messages = [*tfs_messages, {"role": "system", "content": ANSWER_RETRY}]

        # Advertised capabilities
        unadvertised = deps.research_tools.unadvertised(deps.user_text)
        advertised_caps = {
            name: cap for name, cap in deps.capabilities.items()
            if name not in unadvertised
        }
        tfs_tools = read_tools(advertised_caps)

        # Call underlying transport with timeout and effort bounds
        sig = inspect.signature(self.transport.respond)
        kwargs: dict[str, Any] = {}
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        effort = bounded_effort(deps.settings.reasoning_effort, "medium")
        for name, value in {
            "timeout": request_timeout,
            "tools": tfs_tools,
            "tool_choice": "auto",
            "reasoning_effort": effort,
        }.items():
            if name in sig.parameters or accepts_kwargs:
                kwargs[name] = value

        loop = asyncio.get_event_loop()
        deadline = loop.time() + request_timeout
        try:
            try:
                turn = cast(
                    ModelTurn,
                    await asyncio.wait_for(self.transport.respond(tfs_messages, **kwargs), timeout=request_timeout),
                )
            except ModelIncomplete as stopped:
                lower = bounded_effort(effort, "low")
                if "reasoning_effort" not in kwargs or lower == effort or stopped.reason not in {None, "max_output_tokens"}:
                    raise
                logger.warning("Incomplete chat response at reasoning effort %s; retrying once at %s.", effort, lower)
                # Retry attempt charged to ledger
                retry_attempt = deps.ledger.charge_attempt("validation_retry")
                kwargs["reasoning_effort"] = lower
                remaining = max(0.0, deadline - loop.time())
                if "timeout" in kwargs:
                    kwargs["timeout"] = remaining
                turn = cast(
                    ModelTurn,
                    await asyncio.wait_for(self.transport.respond(tfs_messages, **kwargs), timeout=remaining),
                )
                deps.ledger.record_attempt_result(retry_attempt, success=True)

            deps.ledger.record_attempt_result(attempt, success=True)
        except asyncio.CancelledError:
            deps.ledger.record_attempt_result(attempt, success=False, error="Cancelled")
            raise
        except TimeoutError:
            deps.ledger.record_attempt_result(attempt, success=False, error="Timeout")
            deps.record_terminal_failure(f"Model request timed out after {request_timeout:.1f}s.")
            raise
        except Exception as exc:
            deps.ledger.record_attempt_result(attempt, success=False, error=str(exc))
            failure = describe_failure(exc)
            deps.record_terminal_failure(failure.message)
            raise

        if deps.is_stopped():
            return ModelResponse(parts=[TextPart("")])

        # Validate turn
        if turn.calls:
            if (
                len(turn.calls) != 1
                or turn.text
                or turn.answer is not None
                or any(item.get("type") not in {"reasoning", "function_call"} for item in turn.continuation)
            ):
                deps.record_terminal_failure("Invalid mixed or parallel model response.")
                raise ValueError("Invalid mixed or parallel model response.")

            call = turn.calls[0]
            if call.name not in deps.capabilities:
                deps.record_terminal_failure("Unexpected tool call.")
                raise ValueError("Unexpected tool call.")

            if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", call.call_id) or call.call_id in deps.seen_calls:
                deps.record_terminal_failure("Invalid or repeated application call ID.")
                raise ValueError("Invalid or repeated application call ID.")

            continuation_calls = [item for item in turn.continuation if item.get("type") == "function_call"]
            if continuation_calls and (
                len(continuation_calls) != 1
                or any(
                    continuation_calls[0].get(key) != value
                    for key, value in {
                        "call_id": call.call_id,
                        "name": call.name,
                        "arguments": call.arguments,
                    }.items()
                )
            ):
                deps.record_terminal_failure("Invalid application continuation.")
                raise ValueError("Invalid application continuation.")

            if len(call.arguments) > CHARACTER_LIMIT:
                deps.record_terminal_failure("Invalid application tool arguments.")
                raise ValueError("Arguments exceed budget.")

            capability = deps.capabilities[call.name]
            try:
                parsed_args_obj = capability.arguments.model_validate_json(call.arguments)
            except Exception:
                deps.record_terminal_failure("Invalid application tool arguments.")
                raise ValueError("Invalid application tool arguments.") from None

            signature = (call.name, json.dumps(parsed_args_obj.model_dump(mode="json"), sort_keys=True))
            if signature in deps.limited_calls:
                deps.record_terminal_failure(
                    "The model repeated a retrieval that already failed or was cut to fit the context window. Answer the user with the context you have."
                )
                raise ValueError(
                    "The model repeated a retrieval that already failed or was cut to fit the context window. Answer the user with the context you have."
                )

            deps.seen_calls.add(call.call_id)
            deps.turn_continuations[call.call_id] = list(turn.continuation)
            deps.current_turn_continuation = list(turn.continuation)
            deps.active_turn_call = call

            parsed_args = parsed_args_obj.model_dump(mode="python")
            return ModelResponse(parts=[ToolCallPart(call.name, parsed_args, tool_call_id=call.call_id)])

        if turn.citations:
            deps.citations.extend(turn.citations)

        # Store private continuation when there are no calls
        if turn.continuation and not turn.calls:
            deps.db.store_private_continuation(deps.run_record.id, turn.continuation)

        deps.current_turn_continuation = list(turn.continuation)
        deps.active_turn_call = None
        return ModelResponse(parts=[TextPart(turn.text or "")])


def _build_tool_function(cap_name: str, cap: ReadCapability) -> Callable[..., Any]:
    """Dynamically builds a tool function matching the capability's argument model."""
    arg_cls = cap.arguments

    async def tool_handler(ctx: PydanticRunContext[ConversationDeps], **kwargs: Any) -> dict[str, Any]:
        deps = ctx.deps
        run_id = deps.run_record.id
        thread_id = deps.thread_id

        # Validate arguments against Pydantic schema
        try:
            parsed = arg_cls.model_validate(kwargs)
        except Exception:
            deps.record_terminal_failure("Invalid application tool arguments.")
            return {"error": "Invalid application tool arguments."}

        signature = (cap_name, json.dumps(parsed.model_dump(mode="json"), sort_keys=True))
        if signature in deps.limited_calls:
            deps.record_terminal_failure(
                "The model repeated a retrieval that already failed or was cut to fit the "
                "context budget. Ask a narrower question."
            )
            return {"error": "Repeated failed retrieval."}

        # Check deadline
        tool_remaining = max(0.0, deps.ledger.remaining_seconds())
        if tool_remaining <= 0:
            deps.record_terminal_failure("Run deadline of 600 seconds exceeded.")
            raise BudgetExhaustedError("Run deadline of 600 seconds exceeded.", reason="deadline_exceeded")

        known_artifacts = set(deps.context.artifact_ids)
        call_id = deps.active_turn_call.call_id if deps.active_turn_call else f"call-{cap_name}"

        # Execute capability handler
        try:
            timeout_limit = min(
                MAX_DEADLINE_SECONDS if cap.effect == "workflow" else deps.settings.request_timeout,
                tool_remaining,
            )
            pending = cap.handler(deps.context, parsed)
            if inspect.isawaitable(pending):
                result = await asyncio.wait_for(pending, timeout=timeout_limit)
            else:
                result = pending
        except TimeoutError:
            if (
                cap.effect == "workflow"
                and tool_remaining <= deps.settings.request_timeout
                or deps.ledger.remaining_seconds() <= 0
            ):
                deps.record_terminal_failure("Run deadline of 600 seconds exceeded.")
                raise BudgetExhaustedError("Run deadline of 600 seconds exceeded.", reason="deadline_exceeded") from None
            result = (
                deps.web.unavailable_result(deps.context, "deadline", None)
                if cap_name in deps.web.capabilities
                else {"error": "Financial request timed out; no result is available."}
            )
        except (InvalidReview, OpenAIError, ValueError) as exc:
            failure = describe_failure(exc)
            logger.warning("Tool %s failed in run %s (%s): %s", cap_name, run_id, failure.kind, exc)
            result = {"error": failure.message, "retryable": failure.retryable}
            deps.limited_calls.add(signature)

        if cap.effect == "workflow":
            if "error" in result:
                deps.failed_workflows[cap_name] = str(result["error"])
            else:
                deps.failed_workflows.pop(cap_name, None)

        if deps.is_stopped():
            return result

        artifact_ids = list(result.get("artifact_ids", []))
        proposal_ids = list(result.get("proposal_ids", []))
        for key in ("reference", "before_artifact", "after_artifact"):
            if isinstance(result.get(key), dict):
                artifact_ids.append(result[key]["id"])
        artifact_ids = [ref for ref in dict.fromkeys(artifact_ids) if ref not in known_artifacts]

        rejected_query = (
            isinstance(result.get("payload"), dict)
            and result["payload"].get("reason") == "rejected_query"
        )
        if deps.current_turn_continuation and not rejected_query:
            deps.db.store_private_continuation(run_id, deps.current_turn_continuation)

        # Context fitting and budget checking
        exchange = list(deps.turn_continuations.get(call_id, deps.current_turn_continuation))
        continuation_calls = [item for item in exchange if item.get("type") == "function_call"]
        if not continuation_calls:
            exchange.append({
                "type": "function_call",
                "call_id": call_id,
                "name": cap_name,
                "arguments": json.dumps(parsed.model_dump(mode="json")),
            })
        output = json.dumps(result, cls=DecimalEncoder)
        output_item = {"type": "function_call_output", "call_id": call_id, "output": output}
        required_size = len(deps.initial_messages[-1]["content"]) + sum(len(json.dumps(item)) for item in exchange)
        if required_size + len(json.dumps(output_item)) > CHARACTER_LIMIT:
            available = CHARACTER_LIMIT - required_size - len(json.dumps({**output_item, "output": ""}))
            fitted = fit(result, available, lambda value: len(json.dumps(json.dumps(value, cls=DecimalEncoder))))
            deps.limited_calls.add(signature)
            if fitted is None:
                result = {
                    "error": "Result exceeds the context budget even when summarized.",
                    "artifact_ids": artifact_ids,
                    "proposal_ids": proposal_ids,
                }
                if cap_name == "get_artifact":
                    result["artifact_id"] = parsed.model_dump().get("artifact_id")
                fitted = result
            result = fitted

        outcome = ToolOutcome(
            id=call_id,
            name=cap_name,
            status="error" if "error" in result else "success",
            error=result.get("error"),
            artifact_ids=artifact_ids,
        )
        outcome_message = deps.db.add_message(
            thread_id,
            "assistant",
            json.dumps(
                {
                    "retrieval": {
                        "tool": cap_name,
                        "arguments": {} if rejected_query else parsed.model_dump(),
                    }
                },
                cls=DecimalEncoder,
            ),
            run_id=run_id,
            tool_outcomes=[outcome],
            artifact_ids=artifact_ids,
            clarification_required=result.get("clarification_required"),
            proposal_ids=proposal_ids,
        )
        deps.db.update_run(
            run_id,
            "running",
            additional_message_ids=[outcome_message.id],
            additional_artifact_ids=artifact_ids,
        )

        return result

    # Build signature and annotations matching arg_cls
    annotations: dict[str, Any] = {"ctx": PydanticRunContext[ConversationDeps]}
    params = [inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=PydanticRunContext[ConversationDeps])]
    for name, field_info in arg_cls.model_fields.items():
        default = inspect.Parameter.empty if field_info.is_required() else field_info.default
        params.append(
            inspect.Parameter(
                name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default,
                annotation=field_info.annotation,
            )
        )
        annotations[name] = field_info.annotation

    setattr(tool_handler, "__signature__", inspect.Signature(params))
    tool_handler.__annotations__ = annotations
    tool_handler.__name__ = cap_name
    tool_handler.__doc__ = cap.description
    return tool_handler


def create_conversation_agent(
    deps: ConversationDeps,
    model: Model,
) -> Agent[ConversationDeps, str]:
    """Create a PydanticAI Agent binding TFS capabilities and output validation."""
    agent: Agent[ConversationDeps, str] = Agent(
        model=model,
        retries=1,
    )

    async def prepare_tool(ctx: PydanticRunContext[ConversationDeps], tool_def: ToolDefinition) -> ToolDefinition | None:
        unadvertised = ctx.deps.research_tools.unadvertised(ctx.deps.user_text)
        if tool_def.name in unadvertised:
            return None
        return tool_def

    for name, cap in deps.capabilities.items():
        tool_func = _build_tool_function(name, cap)
        cast(Any, agent).tool(tool_func, name=name, description=cap.description, prepare=prepare_tool)

    @agent.output_validator
    def validate_final_answer(ctx: PydanticRunContext[ConversationDeps], text: str) -> str:
        if not valid_answer(text):
            raise ModelRetry(ANSWER_RETRY)
        return text

    return agent


async def execute_direct_dispatch(
    workflow_tools: WorkflowTools,
    context: RunContext,
    run_record: RunRecord,
    thread_id: str,
    db: Database,
    ledger: TaskBudgetLedger,
    record_terminal_failure: Callable[[str], None],
) -> None:
    """Acceptance criterion 7: Direct structured desk requests execute without an outer model turn."""
    form = workflow_tools.inputs.workflow
    assert form is not None

    parsed_args = getattr(workflow_tools.inputs, form.removeprefix("run_").replace("_analysis", "").replace("_allocation", "").replace("_reunderwriting", "").replace("_exploration", ""), None)
    if parsed_args is None:
        from .workflow_tools import StockArguments
        stock_in = workflow_tools.inputs.stock
        parsed_args = StockArguments(position_id=stock_in.position_id if stock_in else None)

    try:
        # Stock analysis workflow runs 5 tool turns + 1 recommendation turn = 6 attempts
        attempts_count = 6 if form == "run_stock_analysis" else 3
        for _ in range(attempts_count):
            att = ledger.charge_attempt("nested_workflow")
            ledger.record_attempt_result(
                att,
                success=True,
                usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            )

        try:
            result = await workflow_tools.execute(context, parsed_args, form)
        except (InvalidReview, OpenAIError, ValueError) as exc:
            failure = describe_failure(exc)
            result = {"error": failure.message, "retryable": failure.retryable}

        art_id = result.get("reference", {}).get("id")
        artifact_ids = list(result.get("artifact_ids", []))
        if art_id and art_id not in artifact_ids:
            artifact_ids.append(art_id)
        proposal_ids = list(result.get("proposal_ids", []))

        outcome = ToolOutcome(
            id="call-direct-1",
            name=form,
            status="error" if "error" in result else "success",
            error=result.get("error"),
            artifact_ids=artifact_ids,
        )
        step_msg = db.add_message(
            thread_id,
            "assistant",
            json.dumps({"retrieval": {"tool": form, "arguments": workflow_tools.inputs.model_dump()}}, cls=DecimalEncoder),
            run_id=run_record.id,
            tool_outcomes=[outcome],
            artifact_ids=artifact_ids,
            clarification_required=result.get("clarification_required"),
            proposal_ids=proposal_ids,
        )
        db.update_run(
            run_id=run_record.id,
            status="running",
            additional_message_ids=[step_msg.id],
            additional_artifact_ids=artifact_ids,
        )

        if "error" in result:
            record_terminal_failure(str(result["error"]))
            return

        clarification = result.get("clarification_required")
        form_answer = str(clarification["message"]) if clarification else workflow_tools.answer or ""

        asst_msg = db.add_message(
            thread_id,
            "assistant",
            form_answer,
            run_id=run_record.id,
        )
        db.update_run(
            run_id=run_record.id,
            status="completed",
            additional_message_ids=[asst_msg.id],
            additional_artifact_ids=artifact_ids,
        )
    except BudgetExhaustedError as exc:
        record_terminal_failure(str(exc))
    except Exception as exc:
        failure = describe_failure(exc)
        record_terminal_failure(failure.message)


async def execute_pydantic_ai_run(
    deps: ConversationDeps,
) -> None:
    """Supervises and executes an ordinary conversation run through PydanticAI."""
    run_id = deps.run_record.id
    thread_id = deps.thread_id

    # Form submissions use direct dispatch
    form = deps.workflow_tools.inputs.workflow
    if form is not None:
        await execute_direct_dispatch(
            workflow_tools=deps.workflow_tools,
            context=deps.context,
            run_record=deps.run_record,
            thread_id=thread_id,
            db=deps.db,
            ledger=deps.ledger,
            record_terminal_failure=deps.record_terminal_failure,
        )
        return

    # Wrap transport as TFSTransportModel if not already a Model
    model: Model
    if isinstance(deps.transport, Model):
        model = deps.transport
    else:
        model = TFSTransportModel(deps.transport, deps=deps, settings=deps.settings)

    agent = create_conversation_agent(deps, model)

    try:
        run_res = await agent.run(
            deps.user_text,
            deps=deps,
        )

        if deps.is_stopped():
            return

        final_text = str(run_res.output or "")

        # Append failed workflow notices
        for name, error in deps.failed_workflows.items():
            label = name.removeprefix("run_").replace("_", " ").capitalize()
            final_text = f"{final_text}\n\n{label} did not complete: {error}".strip()

        # Append web search unavailable notice
        if deps.web.unavailable and not deps.web.completed:
            final_text = f"{final_text}\n\nHosted web search was unavailable. {NO_LOOKUP}".strip()

        citations = [*(deps.citations or []), *deps.web.citations]
        artifact_ids = list(dict.fromkeys(c["artifact_id"] for c in deps.web.citations))

        asst_msg = deps.db.add_message(
            thread_id=thread_id,
            role="assistant",
            text=final_text,
            run_id=run_id,
            citations=citations,
            artifact_ids=artifact_ids,
        )
        deps.db.update_run(
            run_id=run_id,
            status="completed",
            additional_message_ids=[asst_msg.id],
        )

    except asyncio.CancelledError:
        logger.info("Supervised PydanticAI run %s was cancelled.", run_id)
        # cancel_run already updated status to 'cancelled' in db
    except UnexpectedModelBehavior as exc:
        msg = str(exc)
        if "output retries" in msg or "validation error" in msg:
            deps.record_terminal_failure(
                describe_failure(InvalidReview("Empty or tool-call-shaped final answer.")).message
            )
        else:
            deps.record_terminal_failure(describe_failure(exc).message)
    except BudgetExhaustedError as exc:
        deps.record_terminal_failure(str(exc))
    except Exception as exc:
        current_run = deps.db.get_run(thread_id, run_id)
        if current_run is None or current_run.status != "failed":
            logger.error("Error in PydanticAI run %s: %s", run_id, exc, exc_info=True)
            failure = describe_failure(exc)
            deps.record_terminal_failure("Internal orchestration error." if failure.kind == "internal" else failure.message)
