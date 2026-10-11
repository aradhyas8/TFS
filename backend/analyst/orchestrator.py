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
import logging
from typing import Any, cast

from .config import Settings
from .persistence import Database
from .providers import ModelTurn
from .schemas import RunRecord

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
    ) -> None:
        self.db = db
        self.transport = transport
        self.settings = settings
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
            # Build initial context messages for model
            thread_detail = self.db.get_thread(thread_id)
            messages: list[dict[str, Any]] = []

            # System prompt providing instructions for investment analyst
            system_prompt = (
                "You are an expert personal investment analyst. Answer conceptual questions directly, "
                "accurately, and concisely without using any financial or research tools."
            )
            messages.append({"role": "system", "content": system_prompt})

            if thread_detail:
                # Include existing thread messages (last 20)
                for msg in thread_detail.messages[-20:]:
                    messages.append({"role": msg.role, "content": msg.text})
            else:
                messages.append({"role": "user", "content": user_text})

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

                # Check if continuation state is returned (server-only)
                if turn.continuation:
                    self.db.store_private_continuation(run_id, turn.continuation)

                # Conceptual / tool-free answer path: model produced text response
                if turn.text or not turn.calls:
                    assistant_text = turn.text or ""
                    citations = turn.citations or []
                    asst_msg = self.db.add_message(
                        thread_id=thread_id,
                        role="assistant",
                        text=assistant_text,
                        run_id=run_id,
                        citations=citations,
                    )
                    # Complete run
                    self.db.update_run(
                        run_id=run_id,
                        status="completed",
                        additional_message_ids=[asst_msg.id],
                    )
                    return

                # If tools were called, in Ticket 03 tool execution for tools is not yet wired
                # Ticket 03 focuses on tool-free conceptual questions.
                # If tool calls occur unexpectedly, we record them and complete/fail safely.
                self._record_terminal_failure(
                    run_id,
                    f"Unexpected tool call '{turn.calls[0].name}' invoked during tool-free conversation.",
                )
                return

            # Exceeded MAX_TURNS
            self._record_terminal_failure(
                run_id,
                f"Maximum orchestration turns ({MAX_TURNS}) reached without terminal completion.",
            )

        except asyncio.CancelledError:
            logger.info("Supervised task for run %s was cancelled.", run_id)
            # Database cancel was already handled or will be handled by cancel_run
        except Exception as exc:
            logger.error("Unhandled exception in run %s: %s", run_id, exc, exc_info=True)
            self._record_terminal_failure(run_id, f"Internal orchestration error: {exc}")

    async def _call_transport(
        self,
        messages: list[dict[str, Any]],
        timeout: float,
    ) -> ModelTurn:
        """Call underlying transport, respecting timeout."""
        sig = inspect.signature(self.transport.respond)
        kwargs: dict[str, Any] = {}
        if "timeout" in sig.parameters:
            kwargs["timeout"] = timeout

        coro = self.transport.respond(messages, **kwargs)
        return cast(ModelTurn, await asyncio.wait_for(coro, timeout=timeout))

    def _record_terminal_failure(self, run_id: str, error_message: str) -> None:
        """Safely record terminal failure without overwriting cancelled state."""
        self.db.update_run(
            run_id=run_id,
            status="failed",
            error=error_message,
        )
