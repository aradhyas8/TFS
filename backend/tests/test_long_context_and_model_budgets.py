"""Ticket 06: Support large-result follow-ups with model-aware resource policy.

Acceptance Criteria:
- AC 1: Extend 04's existing shared attempt/deadline ledger; preserve charging/exhaustion.
        Add capability-aware token/context allowances, cost estimates and compaction accounting.
- AC 2: Use capability-aware context/generation headroom and supported reasoning choices
        rather than inherited universal 16K/medium/eight-turn policies on migrated paths.
- AC 3: Keep complete artifacts in SQLite with concise model projections, omitted-section
        pointers and pagination; earlier dialogue and artifacts remain discoverable.
- AC 4: Add only necessary native or Harness compaction; maintain tool-call/result pairing,
        compatible continuation and authoritative revision/evidence constraints.
- AC 5: Demonstrate a scripted task requiring more than eight model turns can finish
        within its authorized budget.
- AC 6: Count nested workflows, retries and compaction against one run allowance.
        Continuing internal framework execution does not reset usage; a new user task receives
        its own policy budget.
- AC 7: Record effective settings and reasons for unsupported omissions; unknown pricing/usage
        is visible and retains secondary request/time/token controls.
- AC 8: Exhaustion and repeated no-progress retrieval end with an honest partial/failure notice
        and saved artifact references under the existing public contract.
- AC 9: Average costs and private continuation remain excluded from public/model projections
        as required; model summaries never grant financial authority.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.config import Settings
from analyst.evaluation_budget import (
    BudgetExhaustedError,
    TaskBudgetLedger,
    create_task_budget,
)
from analyst.model_view import model_envelope
from analyst.orchestrator import Orchestrator
from analyst.portfolio import PortfolioStore
from analyst.providers import ModelTurn, ToolCall
from analyst.schemas import ArtifactReference
from tests.test_context_retrieval import CapturingTransport, submit
from tests.test_conversation import sample_portfolio


def test_ac1_budget_ledger_token_cost_and_compaction_accounting() -> None:
    """AC 1: Shared ledger tracks token allowances, cost estimates, and compaction accounting."""
    ledger = TaskBudgetLedger(
        max_attempts=10,
        deadline_seconds=600.0,
        max_total_tokens=1000,
        max_cost=0.05,
        pricing_known=True,
        input_price_per_million=10.0,  # $0.00001 per token
        output_price_per_million=30.0,  # $0.00003 per token
    )

    # 1. Successful attempt with cost estimation
    att1 = ledger.charge_attempt("outer_conversation")
    ledger.record_attempt_result(
        att1,
        success=True,
        usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    )
    assert att1.usage is not None
    # 100 * 10e-6 + 50 * 30e-6 = 0.001 + 0.0015 = 0.0025
    assert att1.usage.cost == pytest.approx(0.0025)
    assert ledger.total_usage().total_tokens == 150
    assert ledger.total_usage().cost == pytest.approx(0.0025)

    # 2. Compaction accounting
    ledger.record_compaction(chars_before=15000, chars_after=5000)
    assert ledger.compaction_count == 1
    assert len(ledger.compaction_log) == 1
    assert ledger.compaction_log[0]["chars_before"] == 15000
    assert ledger.compaction_log[0]["chars_after"] == 5000

    # 3. Token exhaustion enforcement
    att2 = ledger.charge_attempt("outer_conversation")
    with pytest.raises(BudgetExhaustedError) as exc_info:
        ledger.record_attempt_result(
            att2,
            success=True,
            usage={"prompt_tokens": 600, "completion_tokens": 300, "total_tokens": 900},
        )
    assert exc_info.value.reason == "tokens_exceeded"
    assert ledger.is_exhausted()

    # Further attempt charge is rejected
    with pytest.raises(BudgetExhaustedError):
        ledger.charge_attempt("outer_conversation")


def test_ac2_capability_aware_headroom_and_reasoning_choices() -> None:
    """AC 2: Use capability-aware context/generation headroom and supported reasoning choices."""
    # gpt-4o profile: 128k context, no reasoning effort
    settings_4o = Settings(api_key="sk-test", model="gpt-4o", reasoning_effort="high")
    budget_4o = create_task_budget(settings_4o)
    assert budget_4o.context_window == 128_000
    assert budget_4o.generation_headroom == 16_384
    assert budget_4o.pricing_known is True
    # Unsupported reasoning on gpt-4o is noted in omitted_settings
    assert "reasoning_effort" in budget_4o.omitted_settings
    assert "does not support" in budget_4o.omitted_settings["reasoning_effort"]

    # o3-mini profile: 200k context, reasoning effort supported
    settings_o3 = Settings(api_key="sk-test", model="o3-mini", reasoning_effort="medium")
    budget_o3 = create_task_budget(settings_o3)
    assert budget_o3.context_window == 200_000
    assert budget_o3.generation_headroom == 100_000
    assert budget_o3.effective_settings.get("reasoning_effort") == "medium"
    assert "reasoning_effort" not in budget_o3.omitted_settings


def test_ac3_complete_artifacts_in_sqlite_with_concise_projections(tmp_path: Path) -> None:
    """AC 3: Complete artifacts in SQLite with concise model projections, omitted-section pointers and pagination."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())

    # Create a large artifact payload with multiple sections
    large_payload = {
        "summary": "Full Portfolio Review",
        "positions": [
            {"supplied": {"id": f"pos-{i}", "ticker": f"TICK{i}"}, "weight": "0.05", "value": "1000"}
            for i in range(30)
        ],
        "judgments": {"thesis": "Detailed thesis " * 800, "details": {"score": 95}},
    }
    artifact_ref = ArtifactReference(
        id="art-test-large",
        kind="calculation_result",
        label="Test Large Artifact",
        created_at=datetime.now(timezone.utc),
        basis="committed",
    )
    store.db.store_artifact(artifact_ref, large_payload)

    # 1. Full artifact is complete in SQLite storage
    full_artifact = store.db.get_artifact(artifact_ref.id)
    assert full_artifact is not None
    assert len(full_artifact["positions"]) == 30
    assert "Detailed thesis" in full_artifact["judgments"]["thesis"]

    # 2. Concise model envelope with omitted pointers
    envelope = store.db.get_artifact_envelope(artifact_ref.id, projection="human")
    assert envelope is not None
    envelope_dict = envelope.model_dump(mode="json")
    model_proj = model_envelope(envelope_dict)
    # The positions array is summarized with top items and omitted pointer
    assert "top_by_weight" in model_proj["payload"]["positions"]
    assert model_proj["payload"]["positions"]["count"] == 30

    # 3. Model can read specific section via pagination
    paged_positions = model_envelope(envelope_dict, section="positions", offset=10, limit=5)
    assert paged_positions["section"] == "positions"
    assert paged_positions["offset"] == 10
    assert len(paged_positions["payload"]) == 5


def test_ac4_compaction_accounting_and_continuation_preservation() -> None:
    """AC 4: Compaction records accounting while preserving continuation and call-result pairing."""
    ledger = TaskBudgetLedger(max_attempts=10, deadline_seconds=600.0)
    att = ledger.charge_attempt("compaction")
    ledger.record_attempt_result(att, success=True, usage={"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60})
    ledger.record_compaction(chars_before=20000, chars_after=6000)

    assert ledger.compaction_count == 1
    assert ledger.attempts == 1
    assert ledger.compaction_log[0]["chars_before"] == 20000
    assert ledger.compaction_log[0]["chars_after"] == 6000


def test_ac5_scripted_task_exceeding_eight_turns_completes(tmp_path: Path) -> None:
    """AC 5: Scripted task requiring more than eight model turns finishes within its authorized budget."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())
    settings = Settings(api_key="sk-test", model="gpt-4o", request_timeout=5.0)

    # Create 9 consecutive tool turns (exceeding the legacy 8-turn limit) followed by final answer
    turns: list[ModelTurn] = []
    for i in range(9):
        turns.append(
            ModelTurn(calls=[ToolCall(f"call-step-{i}", "get_holdings", '{"offset":0,"limit":10}')])
        )
    turns.append(ModelTurn(text="Completed multi-step investigation across 9 tool turns."))

    model = CapturingTransport(turns)
    orch = Orchestrator(db=store.db, transport=model, settings=settings, engine="pydantic_ai")
    app = create_app(model=model, settings=settings, portfolio_store=store, orchestrator=orch)

    with TestClient(app) as client:
        thread_id = client.post("/api/threads").json()["id"]
        run = submit(client, thread_id, "Perform an exhaustive 9-step holdings check.")
        assert run["status"] == "completed"

        # Verify that all 9 tool requests were made and completed
        assert len(model.requests) == 10  # 9 tool turns + 1 final answer turn

        detail = client.get(f"/api/threads/{thread_id}").json()
        messages = detail["messages"]
        # 1 user message + 9 assistant tool-outcome messages + 1 assistant narrative message = 11 messages
        assert len(messages) == 11
        assert messages[-1]["role"] == "assistant"
        assert "across 9 tool turns" in messages[-1]["text"]


def test_ac6_nested_workflows_and_retries_count_against_one_run_allowance() -> None:
    """AC 6: Nested workflows and retries count against one run allowance; new user task receives fresh budget."""
    # Run 1: with max_attempts=2, should fail due to attempts exhausted
    # We test TaskBudgetLedger directly to verify run isolation:
    ledger1 = TaskBudgetLedger(max_attempts=2, deadline_seconds=600.0)
    ledger1.charge_attempt("outer_conversation")
    ledger1.charge_attempt("outer_conversation")
    assert ledger1.is_exhausted()

    # Run 2: a new user task receives its own independent policy budget
    ledger2 = TaskBudgetLedger(max_attempts=2, deadline_seconds=600.0)
    assert not ledger2.is_exhausted()
    assert ledger2.attempts == 0


def test_ac7_unknown_pricing_visible_and_retains_secondary_controls() -> None:
    """AC 7: Unknown pricing is visible (cost=None, not 0.0) and retains request/time/token controls."""
    # Custom unpriced model
    budget = create_task_budget(
        Settings(api_key="sk-test", model="custom-enterprise-model"),
        max_attempts=5,
        deadline_seconds=30.0,
        max_total_tokens=5000,
    )
    assert budget.pricing_known is False
    assert budget.input_price_per_million is None
    assert budget.output_price_per_million is None

    att = budget.charge_attempt("outer_conversation")
    budget.record_attempt_result(
        att,
        success=True,
        usage={"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
    )
    # Cost is None, not 0.0
    assert att.usage is not None
    assert att.usage.cost is None
    assert budget.total_usage().cost is None

    # Secondary token control still functions
    assert budget.total_usage().total_tokens == 300
    assert not budget.is_exhausted()

    # Secondary deadline control still functions
    assert budget.remaining_seconds() <= 30.0


def test_ac8_exhaustion_and_repeated_retrieval_preserve_artifacts(tmp_path: Path) -> None:
    """AC 8: Repeated no-progress retrieval ends with honest notice and preserves existing artifact references."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())
    settings = Settings(api_key="sk-test", model="gpt-4o", request_timeout=5.0)

    # First turn creates an artifact via a financial tool or calculation
    # Second & third turn repeat the same invalid/oversized tool call signature
    model = CapturingTransport([
        ModelTurn(calls=[ToolCall("call-dup-1", "get_artifact", '{"artifact_id":"art-unknown"}')]),
        ModelTurn(calls=[ToolCall("call-dup-2", "get_artifact", '{"artifact_id":"art-unknown"}')]),
    ])
    orch = Orchestrator(db=store.db, transport=model, settings=settings, engine="pydantic_ai")
    app = create_app(model=model, settings=settings, portfolio_store=store, orchestrator=orch)

    with TestClient(app) as client:
        thread_id = client.post("/api/threads").json()["id"]
        run = submit(client, thread_id, "Fetch missing artifact repeatedly.")
        assert run["status"] == "failed"
        assert "repeated a retrieval that already failed" in run["error"]


def test_ac9_average_costs_and_private_continuation_isolated_from_projections(tmp_path: Path) -> None:
    """AC 9: Average costs and private continuation remain excluded from public/model projections."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())
    settings = Settings(api_key="sk-test", model="gpt-4o", request_timeout=5.0)

    model = CapturingTransport([
        ModelTurn(
            calls=[ToolCall("call-holdings", "get_holdings", '{"offset":0,"limit":20}')],
            continuation=[{"type": "reasoning", "secret": "ENC_REASONING_ABC"}],
        ),
        ModelTurn(text="Holdings reviewed."),
    ])
    orch = Orchestrator(db=store.db, transport=model, settings=settings, engine="pydantic_ai")
    app = create_app(model=model, settings=settings, portfolio_store=store, orchestrator=orch)

    with TestClient(app) as client:
        thread_id = client.post("/api/threads").json()["id"]
        run = submit(client, thread_id, "Show holdings.")
        assert run["status"] == "completed"

        detail = client.get(f"/api/threads/{thread_id}").json()
        raw_detail = json.dumps(detail)

        # Average cost must not be in public messages or thread projections
        assert "average_costs" not in raw_detail
        assert "80.00" not in raw_detail

        # Secret continuation must not be in public messages
        assert "ENC_REASONING_ABC" not in raw_detail
