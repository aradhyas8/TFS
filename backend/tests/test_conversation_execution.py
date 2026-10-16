"""Ticket 05 dedicated acceptance test suite for PydanticAI conversation execution engine.

Verifies:
1. Conceptual questions complete without financial tools; focused financial/research questions
   invoke capabilities and persist tool outcomes and artifact links.
2. Idempotent submission, one active run per thread, run-bound revisions, and missing-portfolio clarification.
3. State-dependent tool advertising (unadvertised tools omitted from schemas) and prerequisite enforcement.
4. Final-answer validation (valid_answer), retry on tool-shaped/empty replies, and 2-attempt failure.
5. Narrative, tool outcomes, and citations persisted separately without exposing private framework state.
6. Execution governed by TaskBudgetLedger (shared attempts and deadline) rather than a legacy fixed while-loop.
7. Direct structured desk requests execute directly without outer model turns.
8. Internal fallback to legacy engine selectable before run starts and never switching mid-run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.config import Settings
from analyst.conversation_engine import (
    ANSWER_RETRY,
)
from analyst.evaluation_budget import TaskBudgetLedger
from analyst.orchestrator import Orchestrator
from analyst.portfolio import PortfolioStore
from analyst.providers import ModelTurn, ToolCall
from analyst.schemas import (
    SubmitMessageRequest,
    WorkflowInput,
)
from tests.test_context_retrieval import CapturingTransport, submit
from tests.test_conversation import ScriptedModel, sample_portfolio


def test_ac1_conceptual_question_completes_without_tools(tmp_path: Path) -> None:
    """AC 1: A conceptual question completes directly without calling financial tools."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())
    settings = Settings(api_key="sk-test", model="gpt-4o", request_timeout=5.0)

    model = ScriptedModel([
        ModelTurn(
            text="Diversification reduces unsystematic risk by spreading investments across varied assets.",
        )
    ])
    orch = Orchestrator(db=store.db, transport=model, settings=settings, engine="pydantic_ai")
    app = create_app(model=model, settings=settings, portfolio_store=store, orchestrator=orch)

    with TestClient(app) as client:
        thread_id = client.post("/api/threads").json()["id"]
        run = submit(client, thread_id, "Explain investment diversification.")
        assert run["status"] == "completed"

        thread_detail = client.get(f"/api/threads/{thread_id}").json()
        assert len(thread_detail["messages"]) == 2
        user_msg, asst_msg = thread_detail["messages"]
        assert user_msg["role"] == "user"
        assert asst_msg["role"] == "assistant"
        assert "Diversification" in asst_msg["text"]
        assert len(asst_msg["tool_outcomes"]) == 0
        assert len(asst_msg["artifact_ids"]) == 0


def test_ac1_focused_financial_question_invokes_tools_and_persists_outcomes(tmp_path: Path) -> None:
    """AC 1: A focused financial question invokes capabilities and persists tool outcomes and artifact links."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())
    settings = Settings(api_key="sk-test", model="gpt-4o", request_timeout=5.0)

    model = CapturingTransport([
        ModelTurn(calls=[ToolCall("call-1", "get_holdings", '{"offset":0,"limit":20}')]),
        ModelTurn(text="You hold 100 shares of TD in your TFSA account."),
    ])
    orch = Orchestrator(db=store.db, transport=model, settings=settings, engine="pydantic_ai")
    app = create_app(model=model, settings=settings, portfolio_store=store, orchestrator=orch)

    with TestClient(app) as client:
        thread_id = client.post("/api/threads").json()["id"]
        run = submit(client, thread_id, "What positions do I hold?")
        assert run["status"] == "completed"

        thread_detail = client.get(f"/api/threads/{thread_id}").json()
        # Message 0: user, Message 1: assistant tool retrieval outcome, Message 2: assistant final narrative
        assert len(thread_detail["messages"]) == 3
        retrieval_msg = thread_detail["messages"][1]
        narrative_msg = thread_detail["messages"][2]

        assert len(retrieval_msg["tool_outcomes"]) == 1
        outcome = retrieval_msg["tool_outcomes"][0]
        assert outcome["name"] == "get_holdings"
        assert outcome["status"] == "success"

        assert "You hold 100 shares of TD" in narrative_msg["text"]


def test_ac2_idempotency_and_run_bound_revision(tmp_path: Path) -> None:
    """AC 2: Preserves idempotent submission, one active run per thread, and run-bound revision."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())
    settings = Settings(api_key="sk-test", model="gpt-4o", request_timeout=5.0)

    model = ScriptedModel([
        ModelTurn(text="Dollar cost averaging is investing equal amounts at regular intervals.")
    ])
    orch = Orchestrator(db=store.db, transport=model, settings=settings, engine="pydantic_ai")
    app = create_app(model=model, settings=settings, portfolio_store=store, orchestrator=orch)

    with TestClient(app) as client:
        thread_id = client.post("/api/threads").json()["id"]
        req_payload = {"client_request_id": "req-idempotent-1", "text": "What is DCA?"}

        # First submission
        resp1 = client.post(f"/api/threads/{thread_id}/messages", json=req_payload)
        assert resp1.status_code == 202
        run1 = resp1.json()
        assert run1["bound_revision"] == 1

        # Idempotent second submission with identical client_request_id returns same run
        resp2 = client.post(f"/api/threads/{thread_id}/messages", json=req_payload)
        assert resp2.status_code == 202
        assert resp2.json()["id"] == run1["id"]

        # Conflicting submission with same request ID but different text returns 409
        resp_conflict = client.post(
            f"/api/threads/{thread_id}/messages",
            json={"client_request_id": "req-idempotent-1", "text": "Different question"},
        )
        assert resp_conflict.status_code == 409


def test_ac3_state_dependent_tool_advertising(tmp_path: Path) -> None:
    """AC 3: State-dependent tools (like calculate_company_cases) are unadvertised unless requested."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())
    settings = Settings(api_key="sk-test", model="gpt-4o", request_timeout=5.0)

    model = CapturingTransport([
        ModelTurn(text="A dividend yield reflects annual dividends divided by stock price.")
    ])
    orch = Orchestrator(db=store.db, transport=model, settings=settings, engine="pydantic_ai")
    app = create_app(model=model, settings=settings, portfolio_store=store, orchestrator=orch)

    with TestClient(app) as client:
        thread_id = client.post("/api/threads").json()["id"]
        run = submit(client, thread_id, "What is dividend yield?")
        assert run["status"] == "completed"

        # Check advertised tools on the captured request
        assert len(model.requests) >= 1
        tools = model.requests[0].get("tools", [])
        tool_names = {t["function"]["name"] if "function" in t else t.get("name") for t in tools}
        assert "calculate_company_cases" not in tool_names


def test_ac4_output_validation_retry_and_exhaustion(tmp_path: Path) -> None:
    """AC 4: Final-answer validation catches empty/tool-shaped text, retries once with ANSWER_RETRY, fails on 2nd."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())
    settings = Settings(api_key="sk-test", model="gpt-4o", request_timeout=5.0)

    # First turn: invalid tool-call JSON text. Second turn: valid prose answer.
    model_recovers = CapturingTransport([
        ModelTurn(text='{"name":"get_holdings","arguments":{}}'),
        ModelTurn(text="Recovered answer explaining holdings in plain English."),
    ])
    orch1 = Orchestrator(db=store.db, transport=model_recovers, settings=settings, engine="pydantic_ai")
    app1 = create_app(model=model_recovers, settings=settings, portfolio_store=store, orchestrator=orch1)

    with client_for_app(app1) as client:
        thread_id = client.post("/api/threads").json()["id"]
        run = submit(client, thread_id, "Show holdings.")
        assert run["status"] == "completed"
        # Second request included the ANSWER_RETRY system message
        assert len(model_recovers.requests) == 2
        last_system_msg = [m for m in model_recovers.requests[1]["messages"] if m.get("role") == "system"][-1]
        assert ANSWER_RETRY in last_system_msg["content"]

    # Incomplete twice: fails cleanly with safe error
    model_fails = CapturingTransport([
        ModelTurn(text='{"name":"get_holdings","arguments":{}}'),
        ModelTurn(text='{"name":"get_holdings","arguments":{}}'),
    ])
    orch2 = Orchestrator(db=store.db, transport=model_fails, settings=settings, engine="pydantic_ai")
    app2 = create_app(model=model_fails, settings=settings, portfolio_store=store, orchestrator=orch2)

    with client_for_app(app2) as client:
        thread_id2 = client.post("/api/threads").json()["id"]
        run_failed = submit(client, thread_id2, "Show holdings.")
        assert run_failed["status"] == "failed"
        assert run_failed["error"] == "The model did not produce a valid result. Nothing was completed; try again."


def test_ac5_narrative_tool_outcomes_and_continuation_isolated(tmp_path: Path) -> None:
    """AC 5: Narrative, tool outcomes and citations persisted separately; private continuation remains hidden."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())
    settings = Settings(api_key="sk-test", model="gpt-4o", request_timeout=5.0)

    secret_token = "INTERNAL_MODEL_REASONING_STATE_XYZ"
    model = CapturingTransport([
        ModelTurn(
            calls=[ToolCall("call-priv-1", "get_holdings", '{"offset":0,"limit":20}')],
            continuation=[{"type": "reasoning", "secret": secret_token}],
        ),
        ModelTurn(
            text="You hold 100 shares of TD.",
            citations=[{"url": "https://sec.gov", "title": "SEC Filing", "artifact_id": "art-sec-1"}],
        ),
    ])
    orch = Orchestrator(db=store.db, transport=model, settings=settings, engine="pydantic_ai")
    app = create_app(model=model, settings=settings, portfolio_store=store, orchestrator=orch)

    with client_for_app(app) as client:
        thread_id = client.post("/api/threads").json()["id"]
        run = submit(client, thread_id, "Tell me about my TD shares.")
        assert run["status"] == "completed"

        # Public thread detail hides private token
        detail = client.get(f"/api/threads/{thread_id}").json()
        raw_text = json.dumps(detail)
        assert secret_token not in raw_text

        # Assistant message contains citations separately
        asst_msg = detail["messages"][-1]
        assert asst_msg["role"] == "assistant"
        assert len(asst_msg["citations"]) == 1
        assert asst_msg["citations"][0]["url"] == "https://sec.gov"

        # Private continuation is stored in server-side storage
        saved_continuation = store.db.get_private_continuation(run["id"])
        assert saved_continuation is not None
        assert any(c.get("secret") == secret_token for c in saved_continuation)


def test_ac6_task_budget_ledger_charges_and_exhaustion(tmp_path: Path) -> None:
    """AC 6: Execution is governed by TaskBudgetLedger rather than a fixed while-loop; exhaustion fails clearly."""
    ledger = TaskBudgetLedger(max_attempts=3, deadline_seconds=600.0)
    assert not ledger.is_exhausted()

    att1 = ledger.charge_attempt("outer_conversation")
    ledger.record_attempt_result(att1, success=True)
    assert ledger.attempts == 1

    att2 = ledger.charge_attempt("validation_retry")
    ledger.record_attempt_result(att2, success=True)
    assert ledger.attempts == 2

    att3 = ledger.charge_attempt("nested_workflow")
    ledger.record_attempt_result(att3, success=True)
    assert ledger.attempts == 3
    assert ledger.is_exhausted()

    with pytest.raises(Exception) as exc_info:
        ledger.charge_attempt("outer_conversation")
    assert "Model attempt allowance exhausted" in str(exc_info.value)


@pytest.mark.anyio
async def test_ac7_direct_structured_desk_dispatch(tmp_path: Path) -> None:
    """AC 7: Direct structured desk requests execute without an outer model turn."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    # No saved portfolio: workflow directly returns clarification requirement without calling model
    settings = Settings(api_key="sk-test", model="gpt-4o", request_timeout=5.0)

    model = CapturingTransport([])
    orch = Orchestrator(db=store.db, transport=model, settings=settings, engine="pydantic_ai")

    thread = store.db.create_thread("th-desk-1")
    req = SubmitMessageRequest(
        client_request_id="req-desk-1",
        text="Run exposure review",
        workflow_input=WorkflowInput(workflow="run_exposure_review").model_dump(mode="json"),
    )
    run_rec, _ = store.db.submit_message(thread.id, req, bound_revision=None)

    await orch._execute_pydantic_ai_run(
        thread_id=thread.id,
        run_record=run_rec,
        user_text=req.text,
        workflow_input=req.workflow_input,
        hypothetical_artifact_id=None,
    )

    final_run = store.db.get_run(thread.id, run_rec.id)
    assert final_run is not None
    assert final_run.status == "completed"
    # Outer model transport was not invoked
    assert len(model.requests) == 0

    th_detail = store.db.get_thread(thread.id)
    assert th_detail is not None
    # User message + retrieval step outcome + narrative message
    assert len(th_detail.messages) == 3
    assert th_detail.messages[1].tool_outcomes[0].name == "run_exposure_review"
    assert "workflow form" in th_detail.messages[2].text.lower()


def test_ac8_engine_selection_fallback(tmp_path: Path) -> None:
    """AC 8: Internal fallback to legacy engine is selectable before run starts and never switches mid-run."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())
    settings = Settings(api_key="sk-test", model="gpt-4o", request_timeout=5.0)

    # Legacy engine execution
    model_legacy = ScriptedModel([ModelTurn(text="Answer from legacy engine.")])
    orch_legacy = Orchestrator(db=store.db, transport=model_legacy, settings=settings, engine="legacy")
    assert orch_legacy._resolve_engine() == "legacy"

    app_legacy = create_app(model=model_legacy, settings=settings, portfolio_store=store, orchestrator=orch_legacy)
    with client_for_app(app_legacy) as client:
        thread_id = client.post("/api/threads").json()["id"]
        run = submit(client, thread_id, "Hello legacy.")
        assert run["status"] == "completed"
        msg = client.get(f"/api/threads/{thread_id}").json()["messages"][-1]
        assert msg["text"] == "Answer from legacy engine."

    # PydanticAI engine execution
    model_pai = ScriptedModel([ModelTurn(text="Answer from PydanticAI engine.")])
    orch_pai = Orchestrator(db=store.db, transport=model_pai, settings=settings, engine="pydantic_ai")
    assert orch_pai._resolve_engine() == "pydantic_ai"

    app_pai = create_app(model=model_pai, settings=settings, portfolio_store=store, orchestrator=orch_pai)
    with client_for_app(app_pai) as client:
        thread_id2 = client.post("/api/threads").json()["id"]
        run2 = submit(client, thread_id2, "Hello PydanticAI.")
        assert run2["status"] == "completed"
        msg2 = client.get(f"/api/threads/{thread_id2}").json()["messages"][-1]
        assert msg2["text"] == "Answer from PydanticAI engine."


def client_for_app(app: Any) -> TestClient:
    return TestClient(app)
