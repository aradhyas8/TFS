"""Ticket 07: Interruption recovery, cancellation safety, and proposal safety.

Acceptance criteria under test:
- AC 1: Cancellation before/during/after a tool call keeps authoritative terminal status
  and rejects late model or handler output.
- AC 2: Reopening SQLite after process interruption preserves conversation, accepted artifacts,
  proposals and usage; interrupted runs are reported honestly without replay.
- AC 3: Persist any required framework state privately with version/engine identity;
  incompatible state fails safely and does not replay effects.
- AC 4: Existing portfolio, receipt, decision and thread data survives restarts and reopenings.
- AC 5: Agent proposals use the committed bound revision even in hypothetical runs;
  identical previews deduplicate, ambiguous accounts clarify, and output retries do not stack quantity changes.
- AC 6: Only the existing user-confirmation endpoint can commit; stale revisions, concurrent confirmation,
  and repeated requests preserve transactional receipts.
- AC 7: No transparent crash replay or new durable engine is introduced. A fresh user retry uses
  the existing idempotency/revision rules and cannot silently reapply previous effects.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.config import Settings
from analyst.financial_data import FakeFinancialProvider
from analyst.orchestrator import Orchestrator
from analyst.persistence import (
    Database,
    IncompatibleStateError,
)
from analyst.portfolio import PortfolioStore
from analyst.providers import ModelTurn, ToolCall
from analyst.schemas import (
    SavedPortfolio,
    SubmitMessageRequest,
)
from tests.test_context_retrieval import CapturingTransport, submit
from tests.test_financial_tools import evidence, portfolio


class BlockingTransport(CapturingTransport):
    """Transport that can block until released or execute custom logic upon receiving a request."""

    def __init__(self, turns: list[Any], released: bool = True) -> None:
        super().__init__(turns)
        self.release = threading.Event()
        self.call_started = threading.Event()
        if released:
            self.release.set()

    async def respond(self, messages: list[dict[str, Any]], **kwargs: Any) -> ModelTurn:
        self.requests.append({"messages": json.loads(json.dumps(messages)), **kwargs})
        self.call_started.set()
        while not self.release.is_set():
            await asyncio.sleep(0.005)
        turn = next(self.turns)
        return turn() if callable(turn) else turn


def wait_until(condition: Any, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.005)
    raise AssertionError("Condition was not reached within timeout.")


def setup_desk(
    tmp_path: Path,
    turns: list[Any],
    saved: SavedPortfolio | None = None,
    engine: str = "pydantic_ai",
    released: bool = True,
) -> tuple[TestClient, BlockingTransport, PortfolioStore]:
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(saved or portfolio())
    transport = BlockingTransport(turns, released=released)
    settings = Settings(api_key="sk-test", model="gpt-4o", request_timeout=5.0)
    financial = FakeFinancialProvider(evidence())
    orch = Orchestrator(
        db=store.db,
        transport=transport,
        settings=settings,
        financial=financial,
        engine=engine,  # type: ignore[arg-type]
    )
    app = create_app(
        model=transport,
        settings=settings,
        portfolio_store=store,
        financial=financial,
        orchestrator=orch,
    )
    client = TestClient(app)
    return client, transport, store


SET_25 = ("prepare_portfolio_proposal", {"edits": [{"action": "set_quantity", "position_id": "stock", "quantity": "25"}]})
CHANGE_5 = ("prepare_portfolio_proposal", {"edits": [{"action": "change_quantity", "position_id": "stock", "quantity": "5"}]})


def make_call(tool: tuple[str, dict[str, Any]], call_id: str = "call-1") -> ModelTurn:
    name, args = tool
    return ModelTurn(calls=[ToolCall(call_id, name, json.dumps(args))])


# -----------------------------------------------------------------------------
# AC 1: Cancellation safety before, during, and after tool calls
# -----------------------------------------------------------------------------


def test_cancellation_before_tool_call_halts_and_rejects_late_output(tmp_path: Path) -> None:
    """AC 1: Cancellation before tool call execution drops late work, runs no handler, and keeps cancelled status."""
    state: dict[str, Any] = {}

    def cancel_then_call() -> ModelTurn:
        # Simulate run being cancelled while model is answering
        for run in state["store"].db.get_thread(state["thread"]).runs:
            state["store"].db.cancel_run(state["thread"], run.id)
        return make_call(SET_25)

    client, transport, state["store"] = setup_desk(
        tmp_path,
        [cancel_then_call, ModelTurn(text="Late answer.")],
    )
    with client:
        state["thread"] = thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Set my Acme shares to 25.")
        assert run["status"] == "cancelled"

        # Proposal handler must NOT have executed
        proposals = client.get("/api/portfolio/proposals").json()
        assert proposals == []

        # No late assistant message
        detail = client.get(f"/api/threads/{thread}").json()
        assert len(detail["messages"]) == 1  # Only user message
        assert detail["runs"][0]["status"] == "cancelled"


def test_cancellation_after_tool_call_drops_late_model_answer(tmp_path: Path) -> None:
    """AC 1: Cancellation after a completed tool call drops the late final model answer and preserves cancelled."""
    client, transport, store = setup_desk(
        tmp_path,
        [
            make_call(CHANGE_5, "call-1"),
            ModelTurn(text="Late final answer that should never be stored."),
        ],
        released=True,
    )
    with client:
        thread = client.post("/api/threads").json()["id"]

        # Intercept before second turn completes by clearing release
        # We start with release set for first turn, but we want to cancel before 2nd turn
        # Let's use a callable for the second turn:
        def answer_turn() -> ModelTurn:
            # At this point, tool call has finished; cancel before answer returns
            th = store.db.get_thread(thread)
            assert th is not None
            for run in th.runs:
                store.db.cancel_run(thread, run.id)
            return ModelTurn(text="Late final answer that should never be stored.")

        transport.turns = iter([make_call(CHANGE_5, "call-1"), cast(Any, answer_turn)])

        run = submit(client, thread, "I bought 5 more Acme shares.")
        assert run["status"] == "cancelled"

        # The proposal from the tool call that finished before cancellation remains stored in pending state
        proposals = client.get("/api/portfolio/proposals").json()
        assert len(proposals) == 1
        assert proposals[0]["status"] == "pending"

        # But NO late narrative assistant text was stored
        detail = client.get(f"/api/threads/{thread}").json()
        texts = [m["text"] for m in detail["messages"] if m["role"] == "assistant" and not m.get("tool_outcomes")]
        assert "Late final answer" not in "".join(texts)
        assert detail["runs"][0]["status"] == "cancelled"


# -----------------------------------------------------------------------------
# AC 2 & AC 3: Interruption recovery, private framework state, and usage persistence
# -----------------------------------------------------------------------------


def test_reopen_sqlite_after_interruption_preserves_conversation_and_usage(tmp_path: Path) -> None:
    """AC 2: Interrupted runs transition to 'interrupted' without replay, preserving messages and usage."""
    db_path = tmp_path / "portfolio.db"
    store = PortfolioStore(database_path=db_path)
    store.save(portfolio())

    # Create run and simulate process interruption by keeping it in 'running' state in DB
    db = store.db
    db.create_thread("th-recover")
    req = SubmitMessageRequest(client_request_id="req-crash-1", text="Question before crash")
    run, _ = db.submit_message("th-recover", req, bound_revision=1)
    db.add_message("th-recover", "user", "Question before crash", run_id=run.id)

    # Record some usage in private_execution_state
    db.store_execution_state(
        run_id=run.id,
        engine="pydantic_ai",
        engine_version="1.0",
        usage={"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160, "cost": 0.005},
        framework_state={"phase": "turn_1"},
    )

    # Now simulate server restarting by calling recover_interrupted_runs
    recovered_count = db.recover_interrupted_runs()
    assert recovered_count == 1

    # Check that run is now reported honestly as interrupted
    reopened_run = db.get_run("th-recover", run.id)
    assert reopened_run is not None
    assert reopened_run.status == "interrupted"

    # Verify usage was preserved across restart
    exec_state = db.get_execution_state(run.id)
    assert exec_state is not None
    assert exec_state["engine"] == "pydantic_ai"
    assert exec_state["engine_version"] == "1.0"
    assert exec_state["usage"]["total_tokens"] == 160
    assert exec_state["usage"]["cost"] == 0.005

    # AC 7: Browser resending identical request gets interrupted run without re-running
    is_again, is_new = db.submit_message("th-recover", req, bound_revision=1)
    assert is_new is False
    assert is_again.id == run.id
    assert is_again.status == "interrupted"

    # A fresh user request with new client_request_id starts cleanly
    fresh_req = SubmitMessageRequest(client_request_id="req-fresh-2", text="Question after restart")
    fresh_run, is_fresh_new = db.submit_message("th-recover", fresh_req, bound_revision=1)
    assert is_fresh_new is True
    assert fresh_run.status == "running"
    assert fresh_run.id != run.id


def test_incompatible_framework_state_fails_safely(tmp_path: Path) -> None:
    """AC 3: Incompatible framework state (unsupported engine or version) fails safely without replaying."""
    db_path = tmp_path / "portfolio.db"
    db = Database(db_path)
    db.initialize()
    db.create_thread("th-compat")
    run, _ = db.submit_message("th-compat", SubmitMessageRequest(client_request_id="req-compat-1", text="test"), 1)

    # Store state with an incompatible engine
    db.store_execution_state(
        run_id=run.id,
        engine="future_unsupported_engine_v99",
        engine_version="99.0",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )

    with pytest.raises(IncompatibleStateError, match="Incompatible"):
        db.validate_execution_state(run.id, expected_engine="pydantic_ai")


def test_data_survival_across_reopen_and_initialization(tmp_path: Path) -> None:
    """AC 4: Prove portfolio, receipts, decisions and threads survive repeated initialization and reopen."""
    db_path = tmp_path / "portfolio.db"
    db = Database(db_path)
    db.initialize()

    # Save a portfolio revision
    port = portfolio()
    store = PortfolioStore(database_path=db_path)
    store.save(port)

    # Create thread and message
    db.create_thread("th-survival")
    run, _ = db.submit_message("th-survival", SubmitMessageRequest(client_request_id="req-s1", text="hello"), 1)
    db.add_message("th-survival", "user", "hello", run_id=run.id)

    # Store a proposal and confirm it to create a receipt
    changed = portfolio()
    changed.snapshot.positions[0].shares = Decimal("99")
    prop = db.store_proposal(
        base_revision=1,
        origin="chat",
        before_portfolio=port,
        after_portfolio=changed,
        differences=[],
        warnings=[],
        thread_id="th-survival",
        run_id=run.id,
    )
    rev2, receipt = db.confirm_proposal(prop.id)
    assert receipt.new_revision == 2

    # Reopen database and reinitialize
    db_reopened = Database(db_path)
    db_reopened.initialize()

    # Verify everything survived
    curr = db_reopened.get_current_portfolio()
    assert curr is not None
    assert curr.revision == 2
    assert curr.portfolio.snapshot.positions[0].shares == Decimal("99")

    receipt_stored = db_reopened.get_proposal_receipt(prop.id)
    assert receipt_stored is not None
    assert receipt_stored.previous_revision == 1
    assert receipt_stored.new_revision == 2

    thread = db_reopened.get_thread("th-survival")
    assert thread is not None
    assert len(thread.messages) == 2
    assert thread.messages[0].text == "hello"


# -----------------------------------------------------------------------------
# AC 5: Proposal revision binding, deduplication, and retry safety
# -----------------------------------------------------------------------------


def test_hypothetical_run_proposals_bind_committed_revision(tmp_path: Path) -> None:
    """AC 5: Agent proposals use the committed bound revision even during hypothetical draft analysis."""
    saved = portfolio()
    saved.snapshot.positions[0].shares = Decimal("10")
    client, transport, store = setup_desk(
        tmp_path,
        [
            make_call(SET_25),
            ModelTurn(text="A preview has been prepared for your review."),
        ],
        saved=saved,
    )
    with client:
        # Create a hypothetical artifact draft with 99 shares
        draft_port = portfolio()
        draft_port.snapshot.positions[0].shares = Decimal("99")
        art_resp = client.post("/api/portfolio/artifacts", json={"portfolio": draft_port.model_dump(mode="json")})
        assert art_resp.status_code == 200
        draft_art_id = art_resp.json()["id"]

        thread = client.post("/api/threads").json()["id"]
        # Run hypothetical analysis with draft_art_id
        run_resp = client.post(
            f"/api/threads/{thread}/messages",
            json={
                "client_request_id": "req-hypo-1",
                "text": "What if I held this draft? Also record 25 Acme shares.",
                "hypothetical_artifact_id": draft_art_id,
            },
        )
        assert run_resp.status_code == 202
        run_id = run_resp.json()["id"]

        wait_until(lambda: client.get(f"/api/threads/{thread}/runs/{run_id}").json()["status"] != "running")

        # Verify proposal was prepared against committed revision 1, NOT against the draft
        [proposal] = client.get("/api/portfolio/proposals").json()
        assert proposal["base_revision"] == 1
        detail = client.get(f"/api/portfolio/proposals/{proposal['id']}").json()
        after_positions = {p["id"]: p for p in detail["after_portfolio"]["snapshot"]["positions"]}
        assert Decimal(str(after_positions["stock"]["shares"])) == Decimal("25")
        # Cash should be from saved portfolio (800), not draft
        assert Decimal(str(after_positions["cash"]["cash"])) == Decimal("800")


def test_identical_previews_deduplicate_and_retries_do_not_stack(tmp_path: Path) -> None:
    """AC 5: Identical previews deduplicate to already_pending, and output retries do not stack share changes."""
    # Model calls CHANGE_5 twice in the same conversation
    client, transport, store = setup_desk(
        tmp_path,
        [
            make_call(CHANGE_5, "call-1"),
            make_call(CHANGE_5, "call-2"),
            ModelTurn(text="Preview ready."),
        ],
    )
    with client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "I bought 5 more Acme shares.")
        assert run["status"] == "completed"

        # Only ONE proposal is pending
        proposals = client.get("/api/portfolio/proposals").json()
        assert len(proposals) == 1
        prop = proposals[0]

        # The shares changed from 10 to 15 (10 + 5), NOT stacked to 20 (10 + 5 + 5)
        detail = client.get(f"/api/portfolio/proposals/{prop['id']}").json()
        after_stock = next(p for p in detail["after_portfolio"]["snapshot"]["positions"] if p["id"] == "stock")
        assert Decimal(str(after_stock["shares"])) == Decimal("15")


# -----------------------------------------------------------------------------
# AC 6: Human confirmation only, concurrent confirmation, and transactional receipts
# -----------------------------------------------------------------------------


def test_only_user_confirmation_endpoint_commits(tmp_path: Path) -> None:
    """AC 6: Only POST /api/portfolio/proposals/{id}/confirm can commit; repeated requests preserve receipt."""
    client, transport, store = setup_desk(
        tmp_path,
        [
            make_call(SET_25),
            ModelTurn(text="Preview ready."),
            # Agent attempts to confirm/commit
            ModelTurn(calls=[ToolCall("call-commit", "confirm_portfolio_proposal", '{"proposal_id":"prop-1"}')]),
        ],
    )
    with client:
        thread = client.post("/api/threads").json()["id"]
        run1 = submit(client, thread, "Record 25 Acme shares.")
        assert run1["status"] == "completed"

        [prop] = client.get("/api/portfolio/proposals").json()
        assert prop["status"] == "pending"

        # Agent cannot commit: conversational assent or commit tool is rejected
        run2 = submit(client, thread, "Yes, commit this now.", "req-2")
        assert run2["status"] == "failed"
        assert "Unexpected tool call" in run2["error"]
        assert client.get("/api/portfolio").json()["revision"] == 1

        # User confirmation endpoint commits successfully
        confirm_url = f"/api/portfolio/proposals/{prop['id']}/confirm"
        first_resp = client.post(confirm_url)
        assert first_resp.status_code == 200
        first_receipt = first_resp.json()["receipt"]
        assert first_receipt["new_revision"] == 2
        assert client.get("/api/portfolio").json()["revision"] == 2

        # Repeated confirmation returns identical receipt (idempotent confirmation)
        second_resp = client.post(confirm_url)
        assert second_resp.status_code == 200
        assert second_resp.json()["receipt"] == first_receipt
        assert client.get("/api/portfolio").json()["revision"] == 2


def test_concurrent_confirmation_preserves_transactional_receipt(tmp_path: Path) -> None:
    """AC 6: Concurrent confirmation calls preserve transactional receipts and update current revision once."""
    client, transport, store = setup_desk(
        tmp_path,
        [
            make_call(SET_25),
            ModelTurn(text="Preview ready."),
        ],
    )
    with client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Record 25 Acme shares.")
        assert run["status"] == "completed"

        [prop] = client.get("/api/portfolio/proposals").json()
        confirm_url = f"/api/portfolio/proposals/{prop['id']}/confirm"

        results: list[Any] = []
        errors: list[Any] = []

        def worker() -> None:
            try:
                resp = client.post(confirm_url)
                results.append(resp)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors
        assert len(results) == 2
        # Both must return 200 (one performs the commit, the second returns the applied proposal receipt idempotently)
        assert all(r.status_code == 200 for r in results)
        receipts = [r.json()["receipt"] for r in results]
        assert receipts[0]["new_revision"] == receipts[1]["new_revision"] == 2
        assert client.get("/api/portfolio").json()["revision"] == 2


def test_stale_revision_confirmation_is_rejected_with_409(tmp_path: Path) -> None:
    """AC 6: Confirmation of a proposal whose base revision is stale returns 409 conflict."""
    client, transport, store = setup_desk(
        tmp_path,
        [
            make_call(SET_25),
            ModelTurn(text="Preview ready."),
        ],
    )
    with client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Record 25 Acme shares.")
        assert run["status"] == "completed"

        [prop] = client.get("/api/portfolio/proposals").json()

        # Concurrently commit a different portfolio revision directly to store
        changed = portfolio()
        changed.snapshot.positions[0].shares = Decimal("500")
        store.save(changed)
        assert client.get("/api/portfolio").json()["revision"] == 2

        # Now try to confirm the earlier proposal prepared against base_revision 1
        confirm_url = f"/api/portfolio/proposals/{prop['id']}/confirm"
        resp = client.post(confirm_url)
        assert resp.status_code == 409
        assert "stale" in resp.text.lower()
        # Holdings remain at revision 2 (500 shares)
        curr = client.get("/api/portfolio").json()
        assert curr["revision"] == 2
        assert Decimal(str(curr["portfolio"]["snapshot"]["positions"][0]["shares"])) == Decimal("500")


def test_output_retry_with_repeated_edit_does_not_stack_shares(tmp_path: Path) -> None:
    """AC 5: When model fails final-answer validation and repeats change_quantity in retry, shares do not stack."""
    # First turn: call CHANGE_5 (buying 5 shares)
    # Second turn: empty/tool-shaped answer triggers validation retry
    # Third turn: model repeats CHANGE_5 in retry, followed by valid answer
    client, transport, store = setup_desk(
        tmp_path,
        [
            make_call(CHANGE_5, "call-1"),
            ModelTurn(text='{"name":"prepare_portfolio_proposal"}'),  # triggers ANSWER_RETRY
            make_call(CHANGE_5, "call-2"),
            ModelTurn(text="All set, 15 shares are now proposed."),
        ],
    )
    with client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "I bought 5 more Acme shares.")
        assert run["status"] == "completed"

        proposals = client.get("/api/portfolio/proposals").json()
        assert len(proposals) == 1
        prop = proposals[0]

        detail = client.get(f"/api/portfolio/proposals/{prop['id']}").json()
        after_stock = next(p for p in detail["after_portfolio"]["snapshot"]["positions"] if p["id"] == "stock")
        # Base was 10. Change was +5. Stacking would produce 20. Must be 15!
        assert Decimal(str(after_stock["shares"])) == Decimal("15")

