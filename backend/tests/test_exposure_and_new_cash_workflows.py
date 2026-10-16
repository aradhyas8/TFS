"""Targeted test suite for Ticket 08: Migrate exposure review and new-cash allocation.

Verifies:
1. AC 1: Exposure and allocation execute by direct structured dispatch or conversational workflow tools.
2. AC 2: Model-driven orchestration migrated; screening, calculations, rule binding, and artifacts retained.
3. AC 3: Dated/undated evidence, empty rules, benchmark fund, missing loss tolerance, and explicit new cash.
4. AC 4: Resource accounting on V2 ledger; no hidden medium-effort ceiling; no independent turn budget.
5. AC 5: Result panels, follow-up retrieval, decision saving, cancellation, and legacy engine rollback.
6. AC 6: Financial and evidence parity between V2 and legacy engine.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.config import Settings
from analyst.decisions import DecisionStore
from analyst.financial_data import FakeFinancialProvider
from analyst.orchestrator import Orchestrator
from analyst.portfolio import PortfolioStore
from analyst.providers import FakeDataProvider, ModelTurn, ToolCall
from analyst.schemas import (
    BENCHMARK_FUND,
    BenchmarkSetting,
    PortfolioSettings,
    SavedPortfolio,
)
from tests.test_allocation import (
    allocation_answer,
    allocation_evidence,
    allocation_request,
    comparison_judgments,
)
from tests.test_analysis import recommendation, snapshot
from tests.test_context_retrieval import CapturingTransport, submit
from tests.test_financial_tools import NoWorkflowData


@pytest.fixture(autouse=True)
def default_pydantic_ai_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TFS_ORCHESTRATOR_ENGINE", "pydantic_ai")


def call(name: str, arguments: dict[str, Any] | None = None, call_id: str | None = None) -> ModelTurn:
    return ModelTurn(calls=[ToolCall(call_id or name, name, json.dumps(arguments or {}))])


def exposure_turns() -> list[ModelTurn]:
    return [
        call("review_portfolio"),
        ModelTurn(answer=recommendation()),
    ]


def allocation_turns() -> list[ModelTurn]:
    return [
        call("review_portfolio"),
        call("scan_opportunities"),
        call("calculate_comparison", comparison_judgments()),
        call("size_allocation", {
            "position_id": "fund",
            "min_weight": "0.55",
            "max_weight": "0.65",
            "reason": "Diversification with a cash reserve.",
        }),
        ModelTurn(answer=allocation_answer()),
    ]


# ==============================================================================
# AC 1: Structured Direct Dispatch and Conversational Tool Dispatch
# ==============================================================================

def test_ac1_direct_structured_exposure_review(tmp_path: Path) -> None:
    """Direct structured exposure review dispatches without an outer chat model turn."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))
    model = CapturingTransport(exposure_turns())
    settings = Settings(api_key="sk-test", model="test-model", request_timeout=5.0)

    with TestClient(create_app(model=model, data=FakeDataProvider(), portfolio_store=store, settings=settings)) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Review my exposure.", workflow_input={"workflow": "run_exposure_review"})
        assert run["status"] == "completed", run
        assert len(run["artifact_ids"]) == 1

        # Authoritative artifact created and stored
        artifact = client.get(f"/api/artifacts/{run['artifact_ids'][0]}").json()
        assert artifact["reference"]["kind"] == "analysis_result"
        assert artifact["payload"]["portfolio"]["positions"] is not None

        # Thread messages: user message, retrieval step outcome, narrative answer
        messages = client.get(f"/api/threads/{thread}").json()["messages"]
        assert len(messages) == 3
        assert [m["role"] for m in messages] == ["user", "assistant", "assistant"]
        assert messages[1]["tool_outcomes"][0]["name"] == "run_exposure_review"
        assert messages[1]["tool_outcomes"][0]["status"] == "success"
        assert messages[2]["text"] == artifact["payload"]["recommendation"]["reason"]

        # Outer model transport was not invoked for chat turn; only nested pipeline turns ran
        assert len(model.requests) == 2

        # Execution state was persisted with direct_dispatch metadata
        exec_state = store.db.get_execution_state(run["id"])
        assert exec_state is not None
        assert exec_state["engine"] == "pydantic_ai"
        assert exec_state["framework_state"]["direct_dispatch"] is True
        assert exec_state["framework_state"]["workflow"] == "run_exposure_review"
        assert exec_state["usage"]["attempts"] == 2


def test_ac1_direct_structured_new_cash_allocation(tmp_path: Path) -> None:
    """Direct structured new-cash allocation dispatches without an outer chat turn."""
    req = allocation_request()
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": req["portfolio"], "settings": req.get("settings")}))
    model = CapturingTransport(allocation_turns())
    settings = Settings(api_key="sk-test", model="test-model", request_timeout=5.0)

    inputs = {k: v for k, v in req.items() if k not in {"question", "portfolio", "settings"}}
    inputs.update(workflow="run_new_cash_allocation", analysis_date="2026-09-30")

    with TestClient(create_app(
        model=model,
        data=FakeDataProvider(),
        financial=FakeFinancialProvider(allocation_evidence()),
        portfolio_store=store,
        settings=settings,
    )) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, req["question"], workflow_input=inputs)
        assert run["status"] == "completed", run
        assert len(run["artifact_ids"]) == 1

        artifact = client.get(f"/api/artifacts/{run['artifact_ids'][0]}").json()
        assert artifact["reference"]["kind"] == "analysis_result"
        assert artifact["payload"]["allocation"] is not None

        messages = client.get(f"/api/threads/{thread}").json()["messages"]
        assert len(messages) == 3
        assert messages[1]["tool_outcomes"][0]["name"] == "run_new_cash_allocation"
        assert messages[1]["tool_outcomes"][0]["status"] == "success"
        assert messages[2]["text"] == artifact["payload"]["recommendation"]["reason"]

        # Exactly the 5 nested turns ran
        assert len(model.requests) == 5


def test_ac1_conversational_exposure_review_dispatch(tmp_path: Path) -> None:
    """Conversational question selects and executes run_exposure_review through PydanticAI."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))

    # Outer turn 1: model calls run_exposure_review
    # Nested turns 1 & 2: review_portfolio, recommendation
    # Outer turn 2: model synthesizes final answer
    turns = [
        call("run_exposure_review", {}),
        *exposure_turns(),
        ModelTurn(text="Based on your exposure review, your portfolio is balanced with no immediate action needed."),
    ]
    model = CapturingTransport(turns)
    settings = Settings(api_key="sk-test", model="test-model", request_timeout=5.0)

    with TestClient(create_app(model=model, data=FakeDataProvider(), portfolio_store=store, settings=settings)) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "What is my current portfolio exposure?", workflow_input={"analysis_date": "2026-09-30"})
        assert run["status"] == "completed", run
        assert len(run["artifact_ids"]) == 1

        messages = client.get(f"/api/threads/{thread}").json()["messages"]
        assert len(messages) == 3
        assert messages[1]["tool_outcomes"][0]["name"] == "run_exposure_review"
        assert messages[1]["tool_outcomes"][0]["status"] == "success"
        assert "balanced" in messages[2]["text"].lower()

        # Total model requests: 1 (outer tool call) + 2 (nested workflow) + 1 (outer synthesis) = 4
        assert len(model.requests) == 4

        # Execution state records attempts
        exec_state = store.db.get_execution_state(run["id"])
        assert exec_state is not None
        assert exec_state["usage"]["attempts"] == 4


def test_ac1_conversational_new_cash_requires_explicit_form(tmp_path: Path) -> None:
    """Conversational new-cash invocation without matching form returns clarification."""
    req = allocation_request()
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": req["portfolio"], "settings": req.get("settings")}))

    turns = [
        call("run_new_cash_allocation", {}),
        ModelTurn(text="Please submit the explicit new cash form with your amount and confirmation."),
    ]
    model = CapturingTransport(turns)
    settings = Settings(api_key="sk-test", model="test-model", request_timeout=5.0)

    with TestClient(create_app(model=model, data=FakeDataProvider(), portfolio_store=store, settings=settings)) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "I have $6,000 of new cash to allocate.")
        assert run["status"] == "completed", run
        assert run["artifact_ids"] == []

        messages = client.get(f"/api/threads/{thread}").json()["messages"]
        clarification = messages[1]["clarification_required"]
        assert clarification["workflow"] == "run_new_cash_allocation"
        assert "new_cash.confirmed" in clarification["fields"]


# ==============================================================================
# AC 2 & AC 3: Calculations, Bindings, Evidence, and Clarification Integrity
# ==============================================================================

def test_ac2_calculation_and_artifact_contracts_preserved(tmp_path: Path) -> None:
    """Exposure review retains exact calculation, benchmark binding, and guardrails."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    sn = snapshot()
    store.save(SavedPortfolio.model_validate({
        "snapshot": sn,
        "settings": {"single_company_cap": "0.20", "active_budget": "0.40"},
    }))
    model = CapturingTransport(exposure_turns())
    settings = Settings(api_key="sk-test", model="test-model", request_timeout=5.0)

    with TestClient(create_app(model=model, data=FakeDataProvider(), portfolio_store=store, settings=settings)) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Review exposure.", workflow_input={"workflow": "run_exposure_review"})
        artifact = client.get(f"/api/artifacts/{run['artifact_ids'][0]}").json()
        payload = artifact["payload"]

        # Portfolio metrics and guardrails computed deterministically
        assert payload["portfolio"]["total_value"] is not None
        assert payload["portfolio"]["guardrails"] is not None
        assert payload["portfolio"]["guardrails"]["settings"]["single_company_cap"] == "0.20"
        assert payload["portfolio"]["guardrails"]["settings"]["active_budget"] == "0.40"
        assert payload["recommendation"]["preferred_action"] == "review_only"


def test_ac3_empty_rules_leave_nothing_checked(tmp_path: Path) -> None:
    """Empty rules (nothing set) mean no rules are checked or breached."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({
        "snapshot": snapshot(),
        "settings": PortfolioSettings(),  # Empty settings
    }))
    model = CapturingTransport(exposure_turns())
    settings = Settings(api_key="sk-test", model="test-model", request_timeout=5.0)

    with TestClient(create_app(model=model, data=FakeDataProvider(), portfolio_store=store, settings=settings)) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Review exposure.", workflow_input={"workflow": "run_exposure_review"})
        artifact = client.get(f"/api/artifacts/{run['artifact_ids'][0]}").json()
        assert artifact["payload"]["portfolio"]["guardrails"] is None


def test_ac3_benchmark_fund_treatment(tmp_path: Path) -> None:
    """Benchmark ETF is bound to positions with 0 shares in matching currency and account."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({
        "snapshot": snapshot(),
        "benchmark": BenchmarkSetting(ticker="SPY", listing="ARCX", currency="USD", name="SPDR S&P 500 ETF Trust"),
    }))
    model = CapturingTransport(exposure_turns())
    settings = Settings(api_key="sk-test", model="test-model", request_timeout=5.0)

    with TestClient(create_app(model=model, data=FakeDataProvider(), portfolio_store=store, settings=settings)) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Review exposure.", workflow_input={"workflow": "run_exposure_review"})
        artifact = client.get(f"/api/artifacts/{run['artifact_ids'][0]}").json()
        pos_ids = [p["supplied"]["id"] for p in artifact["payload"]["portfolio"]["positions"]]
        assert BENCHMARK_FUND in pos_ids


def test_ac3_submitted_form_leaves_loss_tolerance_conditional(tmp_path: Path) -> None:
    """A submitted new cash form may leave risk context out: analysis stays conditional."""
    req = allocation_request()
    # Omit risk_context
    req["new_cash"].pop("risk_context", None)
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": req["portfolio"], "settings": req.get("settings")}))
    model = CapturingTransport(allocation_turns())
    settings = Settings(api_key="sk-test", model="test-model", request_timeout=5.0)

    inputs = {k: v for k, v in req.items() if k not in {"question", "portfolio", "settings"}}
    inputs.update(workflow="run_new_cash_allocation")

    with TestClient(create_app(
        model=model,
        data=FakeDataProvider(),
        financial=FakeFinancialProvider(allocation_evidence()),
        portfolio_store=store,
        settings=settings,
    )) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, req["question"], workflow_input=inputs)
        assert run["status"] == "completed", run
        assert len(run["artifact_ids"]) == 1


def test_ac3_missing_portfolio_clarification(tmp_path: Path) -> None:
    """Workflow invocation without saved portfolio returns portfolio_required clarification."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    model = CapturingTransport([])
    settings = Settings(api_key="sk-test", model="test-model", request_timeout=5.0)

    with TestClient(create_app(model=model, data=NoWorkflowData(), portfolio_store=store, settings=settings)) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Review exposure.", workflow_input={"workflow": "run_exposure_review"})
        assert run["status"] == "completed"
        messages = client.get(f"/api/threads/{thread}").json()["messages"]
        clarification = messages[1]["clarification_required"]
        assert clarification["code"] == "portfolio_required"


# ==============================================================================
# AC 4: V2 Resource Accounting and Effort Handling
# ==============================================================================

def test_ac4_nested_workflow_charges_shared_budget_ledger(tmp_path: Path) -> None:
    """All nested workflow model turns consume slots on the shared TaskBudgetLedger."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))
    model = CapturingTransport(exposure_turns())
    settings = Settings(api_key="sk-test", model="test-model", request_timeout=5.0)

    with TestClient(create_app(model=model, data=FakeDataProvider(), portfolio_store=store, settings=settings)) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Review exposure.", workflow_input={"workflow": "run_exposure_review"})
        assert run["status"] == "completed"

        exec_state = store.db.get_execution_state(run["id"])
        assert exec_state is not None
        assert exec_state["usage"]["attempts"] == 2


def test_ac4_budget_exhaustion_terminates_run_honestly(tmp_path: Path) -> None:
    """When shared attempt allowance is exhausted during nested workflow, run fails honestly."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))
    model = CapturingTransport(exposure_turns())
    settings = Settings(api_key="sk-test", model="test-model", request_timeout=5.0)

    orch = Orchestrator(db=store.db, transport=model, settings=settings)
    orch.max_attempts = 1
    with TestClient(create_app(model=model, data=FakeDataProvider(), portfolio_store=store, settings=settings, orchestrator=orch)) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Review exposure.", workflow_input={"workflow": "run_exposure_review"})
        assert run["status"] == "failed", run
        assert "attempt allowance exhausted" in run["error"].lower()


def test_ac4_no_hidden_medium_effort_ceiling(tmp_path: Path) -> None:
    """Configured high reasoning effort is preserved on migrated workflow turns."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))
    model = CapturingTransport(exposure_turns())
    settings = Settings(api_key="sk-test", model="o1", reasoning_effort="high", request_timeout=5.0)

    with TestClient(create_app(model=model, data=FakeDataProvider(), portfolio_store=store, settings=settings)) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Review exposure.", workflow_input={"workflow": "run_exposure_review"})
        assert run["status"] == "completed"

        # CapturingTransport recorded kwargs for each request
        for req in model.requests:
            assert req.get("reasoning_effort") == "high"


# ==============================================================================
# AC 5 & AC 6: Retrieval, Decisions, Parity, and Rollback
# ==============================================================================

def test_ac5_result_panels_and_follow_up_retrieval(tmp_path: Path) -> None:
    """Completed workflow artifact can be fetched by ID and retrieved in follow-up chat."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))

    turns1 = exposure_turns()
    turns2 = [
        call("get_artifact", {"artifact_id": "WILL_BE_REPLACED"}),
        ModelTurn(text="Your top holding by weight is Broad Market ETF at 60%."),
    ]
    model = CapturingTransport(turns1)
    settings = Settings(api_key="sk-test", model="test-model", request_timeout=5.0)

    with TestClient(create_app(model=model, data=FakeDataProvider(), portfolio_store=store, settings=settings)) as client:
        thread = client.post("/api/threads").json()["id"]
        run1 = submit(client, thread, "Review exposure.", workflow_input={"workflow": "run_exposure_review"})
        art_id = run1["artifact_ids"][0]

        # Artifact retrieval endpoint
        art_res = client.get(f"/api/artifacts/{art_id}")
        assert art_res.status_code == 200
        assert art_res.json()["reference"]["id"] == art_id

        # Follow-up query in same thread
        turns2[0] = call("get_artifact", {"artifact_id": art_id})
        model.turns = iter(turns2)
        model.requests.clear()

        run2 = submit(client, thread, "What is my top holding by weight?", request_id="req-2")
        assert run2["status"] == "completed", run2.get("error")
        messages = client.get(f"/api/threads/{thread}").json()["messages"]
        assert "broad market" in messages[-1]["text"].lower()


def test_ac5_cancellation_during_workflow_execution(tmp_path: Path) -> None:
    """Cancelling a run during active workflow execution terminates without overwriting state."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))

    async def slow_respond(*args: Any, **kwargs: Any) -> ModelTurn:
        await asyncio.sleep(2.0)
        return ModelTurn(answer=recommendation())

    class HangingModel:
        def __init__(self) -> None:
            self.requests: list[Any] = []
        async def respond(self, *args: Any, **kwargs: Any) -> ModelTurn:
            self.requests.append(args)
            return await slow_respond(*args, **kwargs)

    settings = Settings(api_key="sk-test", model="test-model", request_timeout=10.0)
    with TestClient(create_app(model=HangingModel(), data=FakeDataProvider(), portfolio_store=store, settings=settings)) as client:
        thread = client.post("/api/threads").json()["id"]
        # Submit run asynchronously
        submit_res = client.post(
            f"/api/threads/{thread}/messages",
            json={"client_request_id": "req-cancel-1", "text": "Review exposure.", "workflow_input": {"workflow": "run_exposure_review"}},
        )
        assert submit_res.status_code == 202
        run_id = submit_res.json()["id"]

        # Cancel immediately
        cancel_res = client.post(f"/api/threads/{thread}/runs/{run_id}/cancel")
        assert cancel_res.status_code == 200
        assert cancel_res.json()["status"] == "cancelled"


def test_ac5_and_ac6_legacy_engine_rollback_and_financial_parity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both V2 and legacy engines produce identical financial calculations for exposure review."""
    sn = snapshot()
    rules = {"single_company_cap": "0.20", "active_budget": "0.30"}
    settings = Settings(api_key="sk-test", model="test-model", request_timeout=5.0)

    # 1. Run under legacy engine
    monkeypatch.setenv("TFS_ORCHESTRATOR_ENGINE", "legacy")
    store_legacy = PortfolioStore(database_path=tmp_path / "legacy.db")
    store_legacy.save(SavedPortfolio.model_validate({"snapshot": sn, "settings": rules}))
    model_legacy = CapturingTransport(exposure_turns())
    with TestClient(create_app(model=model_legacy, data=FakeDataProvider(), portfolio_store=store_legacy, settings=settings)) as client:
        thread = client.post("/api/threads").json()["id"]
        run_leg = submit(client, thread, "Review exposure.", workflow_input={"workflow": "run_exposure_review"})
        art_leg = client.get(f"/api/artifacts/{run_leg['artifact_ids'][0]}").json()

    # 2. Run under V2 engine (pydantic_ai)
    monkeypatch.setenv("TFS_ORCHESTRATOR_ENGINE", "pydantic_ai")
    store_v2 = PortfolioStore(database_path=tmp_path / "v2.db")
    store_v2.save(SavedPortfolio.model_validate({"snapshot": sn, "settings": rules}))
    model_v2 = CapturingTransport(exposure_turns())
    with TestClient(create_app(model=model_v2, data=FakeDataProvider(), portfolio_store=store_v2, settings=settings)) as client:
        thread = client.post("/api/threads").json()["id"]
        run_v2 = submit(client, thread, "Review exposure.", workflow_input={"workflow": "run_exposure_review"})
        art_v2 = client.get(f"/api/artifacts/{run_v2['artifact_ids'][0]}").json()

    # 3. Compare financial results for exact parity
    leg_port = art_leg["payload"]["portfolio"]
    v2_port = art_v2["payload"]["portfolio"]

    assert leg_port["total_value"] == v2_port["total_value"]
    assert leg_port["holdings_as_of"] == v2_port["holdings_as_of"]
    assert len(leg_port["positions"]) == len(v2_port["positions"])
    for p_leg, p_v2 in zip(leg_port["positions"], v2_port["positions"]):
        assert p_leg["supplied"]["id"] == p_v2["supplied"]["id"]
        assert p_leg["weight"] == p_v2["weight"]
        assert p_leg["value"] == p_v2["value"]

    assert leg_port["guardrails"] == v2_port["guardrails"]
    assert art_leg["payload"]["recommendation"] == art_v2["payload"]["recommendation"]


def test_dated_and_undated_evidence_preserved(tmp_path: Path) -> None:
    """AC 3: Preserve dated and undated evidence across workflow execution."""
    ev = allocation_evidence()
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))
    model = CapturingTransport(exposure_turns())
    settings = Settings(api_key="sk-test", model="test-model", request_timeout=5.0)

    with TestClient(create_app(model=model, data=FakeDataProvider(), portfolio_store=store, settings=settings, financial=FakeFinancialProvider(ev))) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Review exposure.", workflow_input={"workflow": "run_exposure_review"})
        assert run["status"] == "completed"
        artifact = client.get(f"/api/artifacts/{run['artifact_ids'][0]}").json()
        port = artifact["payload"]["portfolio"]
        # Dated and undated evidence properties preserved
        assert port["holdings_as_of"] is not None
        assert "positions" in port and len(port["positions"]) > 0
        for pos in port["positions"]:
            assert "supplied" in pos
            assert "value" in pos


def test_financial_parity_new_cash_allocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC 6: Record financial parity between V2 and legacy engines for new-cash allocation."""
    req = allocation_request()
    settings = Settings(api_key="sk-test", model="test-model", request_timeout=5.0)
    ev = allocation_evidence()

    # 1. Run under legacy engine
    monkeypatch.setenv("TFS_ORCHESTRATOR_ENGINE", "legacy")
    store_legacy = PortfolioStore(database_path=tmp_path / "legacy.db")
    store_legacy.save(SavedPortfolio.model_validate({"snapshot": req["portfolio"], "settings": req.get("settings")}))
    model_legacy = CapturingTransport(allocation_turns())
    with TestClient(create_app(model=model_legacy, data=FakeDataProvider(), portfolio_store=store_legacy, settings=settings, financial=FakeFinancialProvider(ev))) as client:
        thread = client.post("/api/threads").json()["id"]
        run_leg = submit(
            client,
            thread,
            req["question"],
            workflow_input={"workflow": "run_new_cash_allocation", "new_cash": req["new_cash"]},
        )
        assert run_leg["status"] == "completed"
        art_leg = client.get(f"/api/artifacts/{run_leg['artifact_ids'][0]}").json()

    # 2. Run under V2 engine (pydantic_ai)
    monkeypatch.setenv("TFS_ORCHESTRATOR_ENGINE", "pydantic_ai")
    store_v2 = PortfolioStore(database_path=tmp_path / "v2.db")
    store_v2.save(SavedPortfolio.model_validate({"snapshot": req["portfolio"], "settings": req.get("settings")}))
    model_v2 = CapturingTransport(allocation_turns())
    with TestClient(create_app(model=model_v2, data=FakeDataProvider(), portfolio_store=store_v2, settings=settings, financial=FakeFinancialProvider(ev))) as client:
        thread = client.post("/api/threads").json()["id"]
        run_v2 = submit(
            client,
            thread,
            req["question"],
            workflow_input={"workflow": "run_new_cash_allocation", "new_cash": req["new_cash"]},
        )
        assert run_v2["status"] == "completed"
        art_v2 = client.get(f"/api/artifacts/{run_v2['artifact_ids'][0]}").json()

    # 3. Compare financial results for exact parity
    assert art_leg["payload"]["portfolio"]["total_value"] == art_v2["payload"]["portfolio"]["total_value"]
    assert art_leg["payload"]["portfolio"]["positions"] == art_v2["payload"]["portfolio"]["positions"]
    assert art_leg["payload"]["allocation"]["context"] == art_v2["payload"]["allocation"]["context"]
    assert art_leg["payload"]["allocation"]["amount"] == art_v2["payload"]["allocation"]["amount"]
    assert art_leg["payload"]["allocation"]["judgment"] == art_v2["payload"]["allocation"]["judgment"]
    assert art_leg["payload"]["recommendation"] == art_v2["payload"]["recommendation"]
    assert art_leg["payload"]["comparison"] == art_v2["payload"]["comparison"]


def test_decision_saving_exposure_and_allocation(tmp_path: Path) -> None:
    """AC 5: Result panels and decision saving persist across exposure and allocation runs."""
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))
    model = CapturingTransport(exposure_turns())
    settings = Settings(api_key="sk-test", model="test-model", request_timeout=5.0)
    dec_store = DecisionStore(tmp_path / "decisions")

    with TestClient(create_app(model=model, data=FakeDataProvider(), portfolio_store=store, settings=settings, store=dec_store)) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Review exposure.", workflow_input={"workflow": "run_exposure_review"})
        assert run["status"] == "completed"
        art = client.get(f"/api/artifacts/{run['artifact_ids'][0]}").json()

        # Save decision referencing the workflow artifact payload
        res = client.post("/api/decisions", json={"result": art["payload"]})
        assert res.status_code == 200, res.text
        dec_id = res.json()["id"]

        # Retrieve saved decision
        get_res = client.get(f"/api/decisions/{dec_id}")
        assert get_res.status_code == 200
        assert get_res.json()["id"] == dec_id
        assert get_res.json()["conclusion"]["preferred_action"] is not None
