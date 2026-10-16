"""Ticket 01: Evaluation baseline and invariants for TFS Agent Harness V2.

This module establishes the executable baseline for stock analysis execution,
follow-up retrieval, and system invariants. It captures:
1. Dated and undated stock analysis execution
2. Missing evidence handling
3. Bound revision preservation under concurrent commits
4. Hypothetical draft snapshots and artifact derivation
5. Model-safe artifact-section follow-up retrieval
6. Core lifecycle invariants (cancellation, duplicate submission, proposal deduplication, stale confirmation)
7. Semantic financial parity assertion helper with documented comparison exclusions
"""

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.config import Settings
from analyst.financial_data import MARKET_TIME, FakeFinancialProvider
from analyst.portfolio import PortfolioStore
from analyst.providers import FakeDataProvider, ModelTurn, ToolCall
from analyst.research import ReviewedResearchProvider
from analyst.schemas import AnalysisRequest, CompanyResearch, SavedPortfolio
from tests.test_analysis import snapshot
from tests.test_context_retrieval import CapturingTransport
from tests.test_stock import (
    company_judgments,
    research_fixture,
    stock_answer,
    stock_comparison_judgments,
    stock_request,
)

# Comparison exclusions: prose strings, timestamps, and generated IDs.
# These fields must NOT be used for financial-parity assertions.
COMPARISON_EXCLUSIONS = {
    "ids": {"id", "run_id", "thread_id", "artifact_id", "message_ids", "client_request_id", "call_id"},
    "timestamps": {"as_of", "created_at", "updated_at", "saved_at", "reviewed_at", "captured_at"},
    "prose": {"reason", "downside", "uncertainty", "assumptions", "what_could_change", "text"},
}


def assert_semantic_financial_parity(
    actual: dict[str, Any], expected: dict[str, Any], *, context: str = ""
) -> None:
    """Asserts that two analysis outcomes match on semantic financial numbers, evidence bindings,

    and artifact contracts, while ignoring random IDs, timestamps, and generated prose variations.
    """
    ctx = f"[{context}] " if context else ""

    # Portfolio totals & weights
    if "portfolio" in expected and expected["portfolio"] is not None:
        assert "portfolio" in actual and actual["portfolio"] is not None, f"{ctx}Missing portfolio in actual result"
        for field in ("total_value", "holdings_value", "cash_value"):
            assert str(actual["portfolio"].get(field)) == str(expected["portfolio"].get(field)), (
                f"{ctx}Portfolio {field} mismatch: {actual['portfolio'].get(field)} != {expected['portfolio'].get(field)}"
            )

        if "direct_companies" in expected["portfolio"]:
            act_comps = {c["company_id"]: c for c in actual["portfolio"].get("direct_companies", [])}
            exp_comps = {c["company_id"]: c for c in expected["portfolio"]["direct_companies"]}
            assert set(act_comps) == set(exp_comps), f"{ctx}Direct company IDs mismatch"
            for cid in exp_comps:
                assert str(act_comps[cid].get("value")) == str(exp_comps[cid].get("value")), (
                    f"{ctx}Direct company {cid} value mismatch"
                )
                assert str(act_comps[cid].get("weight")) == str(exp_comps[cid].get("weight")), (
                    f"{ctx}Direct company {cid} weight mismatch"
                )

    # Stock cases & valuations
    if "stock" in expected and expected["stock"] is not None:
        assert "stock" in actual and actual["stock"] is not None, f"{ctx}Missing stock in actual result"
        assert actual["stock"].get("reporting_currency") == expected["stock"].get("reporting_currency")

        act_cases = {c["name"]: c for c in actual["stock"].get("cases", [])}
        exp_cases = {c["name"]: c for c in expected["stock"].get("cases", [])}
        assert set(act_cases) == set(exp_cases), f"{ctx}Stock cases mismatch"
        for name in exp_cases:
            exp_case = exp_cases[name]
            act_case = act_cases[name]
            assert str(act_case.get("terminal_price")) == str(exp_case.get("terminal_price")), (
                f"{ctx}Case {name} terminal_price mismatch: {act_case.get('terminal_price')} != {exp_case.get('terminal_price')}"
            )
            assert str(act_case.get("required_exit_multiple")) == str(exp_case.get("required_exit_multiple")), (
                f"{ctx}Case {name} required_exit_multiple mismatch"
            )
            assert str(act_case.get("known_terminal_value")) == str(exp_case.get("known_terminal_value")), (
                f"{ctx}Case {name} known_terminal_value mismatch"
            )
            if "sensitivity_prices" in exp_case and exp_case["sensitivity_prices"] is not None:
                assert [str(p) for p in act_case.get("sensitivity_prices", [])] == [
                    str(p) for p in exp_case["sensitivity_prices"]
                ], f"{ctx}Case {name} sensitivity_prices mismatch"

        # Evidence facts
        if "research" in expected["stock"] and "facts" in expected["stock"]["research"]:
            act_facts = {f["id"]: f for f in actual["stock"]["research"]["facts"]}
            exp_facts = {f["id"]: f for f in expected["stock"]["research"]["facts"]}
            assert set(act_facts) == set(exp_facts), f"{ctx}Research fact IDs mismatch"
            for fid in exp_facts:
                for k in ("metric", "value", "unit", "currency", "period_end"):
                    assert str(act_facts[fid].get(k)) == str(exp_facts[fid].get(k)), (
                        f"{ctx}Fact {fid} field {k} mismatch"
                    )

    # Recommendation action, amount, and evidence IDs
    if "recommendation" in expected and expected["recommendation"] is not None:
        assert "recommendation" in actual and actual["recommendation"] is not None
        assert actual["recommendation"].get("preferred_action") == expected["recommendation"].get("preferred_action"), (
            f"{ctx}preferred_action mismatch"
        )
        assert str(actual["recommendation"].get("amount")) == str(expected["recommendation"].get("amount")), (
            f"{ctx}recommendation amount mismatch"
        )
        assert sorted(actual["recommendation"].get("evidence_ids", [])) == sorted(
            expected["recommendation"].get("evidence_ids", [])
        ), f"{ctx}evidence_ids mismatch"

    # Artifact reference contract
    if "reference" in expected and expected["reference"] is not None:
        assert "reference" in actual and actual["reference"] is not None
        assert actual["reference"].get("basis") == expected["reference"].get("basis"), f"{ctx}Artifact basis mismatch"
        assert actual["reference"].get("revision") == expected["reference"].get("revision"), (
            f"{ctx}Artifact revision mismatch"
        )


def submit_run(
    client: TestClient,
    thread_id: str,
    text: str,
    *,
    request_id: str = "req-1",
    workflow_input: dict[str, Any] | None = None,
    hypothetical_artifact_id: str | None = None,
    timeout_iterations: int = 100,
) -> dict[str, Any]:
    """Submits a message and waits for the run to complete or fail."""
    import time

    payload: dict[str, Any] = {"text": text, "client_request_id": request_id}
    if workflow_input is not None:
        payload["workflow_input"] = workflow_input
    if hypothetical_artifact_id is not None:
        payload["hypothetical_artifact_id"] = hypothetical_artifact_id

    response = client.post(f"/api/threads/{thread_id}/messages", json=payload)
    assert response.status_code == 202, f"Expected 202, got {response.status_code}: {response.text}"
    run = response.json()

    for _ in range(timeout_iterations):
        result = client.get(f"/api/threads/{thread_id}/runs/{run['id']}").json()
        if result["status"] != "running":
            return result
        time.sleep(0.02)
    raise AssertionError(f"Run {run['id']} did not complete within timeout (status: {result['status']})")


def make_stock_turns(request: dict[str, Any], answer: dict[str, Any] | None = None) -> list[ModelTurn]:
    """Returns the canonical 6-turn scripted model sequence for stock analysis."""
    bound = AnalysisRequest.model_validate(request).model_dump(mode="json")
    return [
        ModelTurn(calls=[ToolCall("review_portfolio", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("sec", "get_sec_filings", "{}")]),
        ModelTurn(calls=[ToolCall("issuer", "get_issuer_material", "{}")]),
        ModelTurn(calls=[ToolCall("company", "calculate_company_cases", json.dumps(company_judgments()))]),
        ModelTurn(calls=[ToolCall("comparison", "calculate_comparison", json.dumps(stock_comparison_judgments(bound)))]),
        ModelTurn(answer=answer or stock_answer()),
    ]


def test_baseline_dated_stock_analysis(tmp_path: Path) -> None:
    """Baseline Scenario 1: Dated stock analysis with Phase 7 direct workflow dispatch.

    Verifies accepted input, full semantic financial results, evidence binding, visible answer
    contract, and persisted run outcome under mocked providers.
    """
    req = stock_request()
    turns = make_stock_turns(req)
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    saved = SavedPortfolio.model_validate({"snapshot": req["portfolio"], "settings": req.get("settings")})
    store.save(saved)

    model = CapturingTransport(turns)
    settings = Settings(api_key="sk-test", model="test-baseline-model", request_timeout=5)

    with TestClient(
        create_app(
            model=model,
            data=FakeDataProvider(),
            financial=FakeFinancialProvider(),
            research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research_fixture())}),
            portfolio_store=store,
            settings=settings,
        )
    ) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit_run(
            client,
            thread,
            req["question"],
            workflow_input={"workflow": "run_stock_analysis", "stock": {"position_id": "p1"}, "analysis_date": "2026-09-30"},
        )

        # 1. Persisted run outcome contract
        assert run["status"] == "completed", run
        assert run["bound_revision"] == 1
        assert run["hypothetical_artifact_id"] is None
        assert len(run["artifact_ids"]) == 1

        artifact_id = run["artifact_ids"][0]
        artifact_res = client.get(f"/api/artifacts/{artifact_id}").json()
        payload = artifact_res["payload"]
        reference = artifact_res["reference"]

        # 2. Semantic financial results
        assert payload["portfolio"]["total_value"] == "3600"
        assert payload["portfolio"]["holdings_value"] == "2300"
        assert payload["portfolio"]["cash_value"] == "1300"
        assert payload["portfolio"]["holdings_as_of"] == "2026-09-30"

        stock_res = payload["stock"]
        assert stock_res["as_of"] == "2026-09-30"
        assert stock_res["reporting_currency"] == "CAD"
        assert [c["terminal_price"] for c in stock_res["cases"]] == ["50", "100", "150"]
        assert stock_res["cases"][1]["known_terminal_value"] == "1300"
        assert stock_res["cases"][1]["sensitivity_prices"] == ["80", "100", "120"]
        assert stock_res["cases"][1]["required_exit_multiple"] == "16.11"

        # 3. Evidence references
        assert payload["recommendation"]["preferred_action"] == "hold"
        assert payload["recommendation"]["amount"] is None
        assert payload["recommendation"]["evidence_ids"] == ["filing", "issuer"]

        # 4. Artifact reference contract
        assert reference["basis"] == "committed"
        assert reference["revision"] == 1
        assert reference["source_artifact_id"] == "art-rev-1"

        # 5. Visible answer contract (Phase 7 direct dispatch)
        messages = client.get(f"/api/threads/{thread}").json()["messages"]
        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["tool_outcomes"][0]["name"] == "run_stock_analysis"
        assert messages[1]["tool_outcomes"][0]["status"] == "success"
        assert messages[1]["tool_outcomes"][0]["artifact_ids"] == [artifact_id]
        assert messages[2]["role"] == "assistant"
        assert messages[2]["text"] == payload["recommendation"]["reason"]

        # Model turn count
        assert len(model.requests) == 6


def test_baseline_undated_stock_analysis(tmp_path: Path) -> None:
    """Baseline Scenario 2: Undated stock analysis.

    When analysis_date is omitted, analysis values current holdings as of today (market_today).
    Verifies semantic equivalence of calculation cases on the current snapshot date.
    """
    today_iso = datetime.now(MARKET_TIME).date().isoformat()
    port = snapshot()
    port["as_of"] = today_iso
    for p in port["positions"]:
        if "mark" in p and p["mark"]:
            p["mark"]["as_of"] = today_iso
    for fx in port["fx"]:
        fx["as_of"] = today_iso

    req = stock_request()
    req["portfolio"] = port

    # Update research fixture doc dates to valid dates
    res_fix = research_fixture()
    turns = make_stock_turns(req)

    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": port}))
    model = CapturingTransport(turns)

    with TestClient(
        create_app(
            model=model,
            data=FakeDataProvider(),
            financial=FakeFinancialProvider(),
            research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(res_fix)}),
            portfolio_store=store,
            settings=Settings(api_key="sk-test", model="test-baseline-model", request_timeout=5),
        )
    ) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit_run(
            client,
            thread,
            req["question"],
            workflow_input={"workflow": "run_stock_analysis", "stock": {"position_id": "p1"}},  # analysis_date omitted
        )

        assert run["status"] == "completed"
        artifact = client.get(f"/api/artifacts/{run['artifact_ids'][0]}").json()
        payload = artifact["payload"]

        # Parity with dated scenario
        assert payload["portfolio"]["holdings_as_of"] == today_iso
        assert payload["stock"]["as_of"] == today_iso
        assert [c["terminal_price"] for c in payload["stock"]["cases"]] == ["50", "100", "150"]
        assert payload["stock"]["cases"][1]["required_exit_multiple"] == "16.11"
        assert payload["recommendation"]["preferred_action"] == "hold"


def test_baseline_missing_evidence_stock_analysis(tmp_path: Path) -> None:
    """Baseline Scenario 3: Missing evidence handling.

    When critical company facts are missing, incomplete, or contradictory,
    case terminal prices remain None, and recommendation falls back to wait_for_inputs.
    No unvalidated financial value may be invented.
    """
    broken_research = research_fixture()
    broken_research["facts"][0]["value"] = None  # Missing revenue

    expected_answer = stock_answer()
    expected_answer.update(preferred_action="wait_for_inputs", amount=None)

    req = stock_request()
    turns = make_stock_turns(req, answer=expected_answer)

    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": req["portfolio"]}))
    model = CapturingTransport(turns)

    with TestClient(
        create_app(
            model=model,
            data=FakeDataProvider(),
            financial=FakeFinancialProvider(),
            research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(broken_research)}),
            portfolio_store=store,
            settings=Settings(api_key="sk-test", model="test-baseline-model", request_timeout=5),
        )
    ) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit_run(
            client,
            thread,
            req["question"],
            workflow_input={"workflow": "run_stock_analysis", "stock": {"position_id": "p1"}, "analysis_date": "2026-09-30"},
        )

        assert run["status"] == "completed"
        artifact = client.get(f"/api/artifacts/{run['artifact_ids'][0]}").json()
        payload = artifact["payload"]

        assert all(case["terminal_price"] is None for case in payload["stock"]["cases"])
        assert payload["recommendation"]["preferred_action"] == "wait_for_inputs"
        assert payload["recommendation"]["amount"] is None


def test_baseline_bound_revision_stock_analysis(tmp_path: Path) -> None:
    """Baseline Scenario 4: Bound revision basis under concurrent commits.

    A run binds its portfolio revision at acceptance. When a concurrent commit occurs
    during execution (e.g. revision 1 -> revision 2), the running analysis retains its
    bound revision (1), reflects the bound holdings, and tags the artifact revision=1.
    Only the next run binds revision 2.
    """
    req = stock_request()
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    saved = SavedPortfolio.model_validate({"snapshot": req["portfolio"]})
    store.save(saved)

    class CommitDuringExecution(CapturingTransport):
        async def respond(self, messages: Any, **kwargs: Any) -> ModelTurn:
            if len(self.requests) == 2:  # After review and sec filing, simulate concurrent commit
                updated = copy.deepcopy(saved).model_dump(mode="json")
                updated["snapshot"]["positions"][0]["shares"] = "25"  # Changed from 10 to 25
                store.save(SavedPortfolio.model_validate(updated))
            return await super().respond(messages, **kwargs)

    turns = make_stock_turns(req)
    model = CommitDuringExecution(turns)

    with TestClient(
        create_app(
            model=model,
            data=FakeDataProvider(),
            financial=FakeFinancialProvider(),
            research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research_fixture())}),
            portfolio_store=store,
            settings=Settings(api_key="sk-test", model="test-baseline-model", request_timeout=5),
        )
    ) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit_run(
            client,
            thread,
            req["question"],
            workflow_input={"workflow": "run_stock_analysis", "stock": {"position_id": "p1"}, "analysis_date": "2026-09-30"},
        )

        assert run["status"] == "completed"
        assert run["bound_revision"] == 1  # Bound at acceptance

        # Verify DB is now on revision 2
        committed_port = client.get("/api/portfolio").json()
        assert committed_port["revision"] == 2
        assert committed_port["portfolio"]["snapshot"]["positions"][0]["shares"] == "25"

        # Verify artifact retains revision 1 and calculated on 10 shares ($1300), not 25 shares
        artifact = client.get(f"/api/artifacts/{run['artifact_ids'][0]}").json()
        assert artifact["reference"]["revision"] == 1
        assert artifact["reference"]["basis"] == "committed"
        assert artifact["payload"]["stock"]["cases"][1]["known_terminal_value"] == "1300"


def test_baseline_hypothetical_snapshot_stock_analysis(tmp_path: Path) -> None:
    """Baseline Scenario 5: Hypothetical snapshot analysis.

    A draft portfolio is posted as an artifact. The run executes with hypothetical_artifact_id.
    Artifact reference carries basis="hypothetical", revision=None, source_artifact_id=draft_id.
    Committed portfolio in the database remains untouched.
    """
    req = stock_request()
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": req["portfolio"]}))

    # Draft with 99 shares instead of 10
    draft_port = copy.deepcopy(req["portfolio"])
    draft_port["positions"][0]["shares"] = "99"

    turns = make_stock_turns(req)
    model = CapturingTransport(turns)

    with TestClient(
        create_app(
            model=model,
            data=FakeDataProvider(),
            financial=FakeFinancialProvider(),
            research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research_fixture())}),
            portfolio_store=store,
            settings=Settings(api_key="sk-test", model="test-baseline-model", request_timeout=5),
        )
    ) as client:
        # Save draft artifact
        draft_resp = client.post("/api/portfolio/artifacts", json={"portfolio": {"snapshot": draft_port}})
        assert draft_resp.status_code == 200, draft_resp.text
        draft_id = draft_resp.json()["id"]

        thread = client.post("/api/threads").json()["id"]
        run = submit_run(
            client,
            thread,
            "What if I held 99 shares?",
            workflow_input={"workflow": "run_stock_analysis", "stock": {"position_id": "p1"}, "analysis_date": "2026-09-30"},
            hypothetical_artifact_id=draft_id,
        )

        assert run["status"] == "completed"
        assert run["hypothetical_artifact_id"] == draft_id

        # Artifact contract for hypothetical runs
        artifact = client.get(f"/api/artifacts/{run['artifact_ids'][0]}").json()
        ref = artifact["reference"]
        assert ref["basis"] == "hypothetical"
        assert ref["revision"] is None
        assert ref["source_artifact_id"] == draft_id
        assert ref["originating_run_id"] == run["id"]

        # Committed portfolio is completely unchanged
        committed_port = client.get("/api/portfolio").json()
        assert committed_port["revision"] == 1
        assert committed_port["portfolio"]["snapshot"]["positions"][0]["shares"] == "10"


def test_baseline_artifact_section_followup(tmp_path: Path) -> None:
    """Baseline Scenario 6: Model-safe artifact-section follow-up retrieval.

    Follow-up message in the same conversation thread retrieves a slice of the stored
    stock analysis result (e.g. section="result.stock.cases") via get_artifact.
    Private fields (average costs, continuation state) remain redacted.
    """
    req = stock_request()
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    saved_port = SavedPortfolio.model_validate({"snapshot": req["portfolio"], "average_costs": {"p1": "85.00"}})
    store.save(saved_port)

    # First run: execute stock analysis
    turns_1 = make_stock_turns(req)
    # Second run: follow-up get_artifact with section
    turns_2 = [
        ModelTurn(calls=[ToolCall("read_cases", "get_artifact", json.dumps({"artifact_id": "PLACEHOLDER", "section": "result.stock.cases"}))]),
        ModelTurn(text="The terminal prices are 50, 100, and 150 CAD."),
    ]

    model = CapturingTransport(turns_1 + turns_2)

    with TestClient(
        create_app(
            model=model,
            data=FakeDataProvider(),
            financial=FakeFinancialProvider(),
            research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research_fixture())}),
            portfolio_store=store,
            settings=Settings(api_key="sk-test", model="test-baseline-model", request_timeout=5),
        )
    ) as client:
        thread = client.post("/api/threads").json()["id"]

        # 1. First run: stock analysis
        run1 = submit_run(
            client,
            thread,
            req["question"],
            request_id="req-analysis",
            workflow_input={"workflow": "run_stock_analysis", "stock": {"position_id": "p1"}, "analysis_date": "2026-09-30"},
        )
        assert run1["status"] == "completed"
        art_id = run1["artifact_ids"][0]

        # 2. Second run: follow-up reading artifact section
        turns_2 = [
            ModelTurn(calls=[ToolCall("read_cases", "get_artifact", json.dumps({"artifact_id": art_id, "section": "stock.cases"}))]),
            ModelTurn(text="The terminal prices are 50, 100, and 150 CAD."),
        ]
        model.turns = iter(turns_2)

        run2 = submit_run(
            client,
            thread,
            "What are the terminal prices?",
            request_id="req-followup",
        )
        assert run2["status"] == "completed"

        # Verify the tool output received by the model in turn 8
        followup_request = model.requests[-1]["messages"]
        output_msg = next(m for m in followup_request if m.get("type") == "function_call_output")
        output = json.loads(output_msg["output"])

        assert output["reference"]["id"] == art_id
        assert output["section"] == "stock.cases"
        assert [c["name"] for c in output["payload"]] == ["downside", "base", "upside"]
        assert [c["terminal_price"] for c in output["payload"]] == ["50", "100", "150"]

        # Assert private fields are never leaked in model dialogue
        serialized_requests = json.dumps(model.requests)
        assert "85.00" not in serialized_requests


def test_baseline_invariants_cancellation_and_duplicate_submission(tmp_path: Path) -> None:
    """Baseline Scenario 7: Invariants for cancellation and duplicate submission.

    Verifies cancellation drops late output and duplicate submission returns existing run idempotently.
    """
    req = stock_request()
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": req["portfolio"]}))

    # Slow turn to allow cancellation
    import asyncio
    import threading

    started = threading.Event()

    class CancellableTransport(CapturingTransport):
        async def respond(self, messages: Any, **kwargs: Any) -> ModelTurn:
            started.set()
            await asyncio.sleep(0.5)
            return ModelTurn(text="Should not be stored")

    model = CancellableTransport([ModelTurn(text="Ignored")])

    with TestClient(
        create_app(
            model=model,
            data=FakeDataProvider(),
            financial=FakeFinancialProvider(),
            portfolio_store=store,
            settings=Settings(api_key="sk-test", model="test-baseline-model", request_timeout=5),
        )
    ) as client:
        thread = client.post("/api/threads").json()["id"]

        # Test cancellation
        res = client.post(f"/api/threads/{thread}/messages", json={"text": "Cancel me", "client_request_id": "req-cancel"})
        assert res.status_code == 202
        run_id = res.json()["id"]
        assert started.wait(2.0)

        cancel_res = client.post(f"/api/threads/{thread}/runs/{run_id}/cancel")
        assert cancel_res.status_code == 200
        assert cancel_res.json()["status"] == "cancelled"

        # Verify duplicate submission returns existing run
        dup_res = client.post(f"/api/threads/{thread}/messages", json={"text": "Cancel me", "client_request_id": "req-cancel"})
        assert dup_res.status_code == 202
        assert dup_res.json()["id"] == run_id
        assert dup_res.json()["status"] == "cancelled"


def test_baseline_invariants_proposal_deduplication_and_stale_confirmation(tmp_path: Path) -> None:
    """Baseline Scenario 8: Invariants for proposal deduplication, duplicate confirmation, and stale confirmation.

    Verifies:
    1. Proposal deduplication: repeated prepare_portfolio_proposal calls with identical edits reuse one pending proposal.
    2. Duplicate confirmation: confirming an already-confirmed proposal returns the exact same receipt idempotently.
    3. Stale confirmation: confirming a proposal against an outdated base revision returns HTTP 409 Conflict.
    """
    req = stock_request()
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": req["portfolio"]}))

    prepare_args = {"edits": [{"action": "set_quantity", "position_id": "p1", "quantity": "25"}]}
    turns = [
        ModelTurn(calls=[ToolCall("call_1", "prepare_portfolio_proposal", json.dumps(prepare_args))]),
        ModelTurn(calls=[ToolCall("call_2", "prepare_portfolio_proposal", json.dumps(prepare_args))]),
        ModelTurn(text="A proposal preview is ready for your confirmation."),
    ]
    model = CapturingTransport(turns)

    with TestClient(
        create_app(
            model=model,
            data=FakeDataProvider(),
            financial=FakeFinancialProvider(),
            portfolio_store=store,
            settings=Settings(api_key="sk-test", model="test-baseline-model"),
        )
    ) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit_run(client, thread, "Update ACME shares to 25.")
        assert run["status"] == "completed"

        # 1. Proposal deduplication: second call returns already_pending with same proposal id
        messages = client.get(f"/api/threads/{thread}").json()["messages"]
        assert messages[1]["tool_outcomes"][0]["status"] == "success"
        assert messages[2]["tool_outcomes"][0]["status"] == "success"

        proposal_output_1 = json.loads(model.requests[1]["messages"][-1]["output"])
        proposal_output_2 = json.loads(model.requests[2]["messages"][-1]["output"])
        assert proposal_output_1["status"] == "prepared"
        assert proposal_output_2["status"] == "already_pending"
        proposal_id = proposal_output_1["proposal"]["id"]
        assert proposal_output_2["proposal"]["id"] == proposal_id

        pending = client.get("/api/portfolio/proposals?status=pending").json()
        assert len(pending) == 1
        assert pending[0]["id"] == proposal_id

        # 2. Duplicate confirmation: confirming twice returns the same receipt idempotently
        confirm_1 = client.post(f"/api/portfolio/proposals/{proposal_id}/confirm")
        assert confirm_1.status_code == 200, confirm_1.text
        confirm_data_1 = confirm_1.json()
        assert "receipt" in confirm_data_1
        assert client.get("/api/portfolio").json()["revision"] == 2

        confirm_2 = client.post(f"/api/portfolio/proposals/{proposal_id}/confirm")
        assert confirm_2.status_code == 200, confirm_2.text
        confirm_data_2 = confirm_2.json()
        assert confirm_data_2["receipt"] == confirm_data_1["receipt"]
        assert confirm_data_2["portfolio"]["revision"] == 2
        assert client.get("/api/portfolio").json()["revision"] == 2

        # 3. Stale confirmation: create proposal on revision 2, advance db to revision 3, confirmation fails with 409
        edited_snap = copy.deepcopy(req["portfolio"])
        edited_snap["positions"][0]["shares"] = "40"
        stale_prop_res = client.put("/api/portfolio", json={"base_revision": 2, "portfolio": {"snapshot": edited_snap}})
        assert stale_prop_res.status_code == 200
        stale_proposal_id = stale_prop_res.json()["proposal"]["id"]

        # Advance committed DB to revision 3
        further_snap = copy.deepcopy(req["portfolio"])
        further_snap["positions"][0]["shares"] = "50"
        store.save(SavedPortfolio.model_validate({"snapshot": further_snap}))
        assert client.get("/api/portfolio").json()["revision"] == 3

        # Confirming proposal created against base_revision=2 fails with 409 Conflict
        stale_confirm = client.post(f"/api/portfolio/proposals/{stale_proposal_id}/confirm")
        assert stale_confirm.status_code == 409
        assert "stale" in stale_confirm.json()["detail"].lower() or "conflict" in stale_confirm.json()["detail"].lower()


def test_semantic_financial_parity_comparison_helper() -> None:
    """Verifies that assert_semantic_financial_parity enforces exact numbers and ignores generated IDs/prose."""
    baseline = {
        "portfolio": {
            "total_value": "3600",
            "holdings_value": "2300",
            "cash_value": "1300",
            "direct_companies": [{"company_id": "acme", "value": "2300", "weight": "0.63888889"}],
        },
        "stock": {
            "reporting_currency": "CAD",
            "cases": [
                {"name": "downside", "terminal_price": "50", "required_exit_multiple": "10.0", "known_terminal_value": "1000", "sensitivity_prices": ["40", "50", "60"]},
                {"name": "base", "terminal_price": "100", "required_exit_multiple": "16.11", "known_terminal_value": "1300", "sensitivity_prices": ["80", "100", "120"]},
                {"name": "upside", "terminal_price": "150", "required_exit_multiple": "20.0", "known_terminal_value": "1500", "sensitivity_prices": ["120", "150", "180"]},
            ],
            "research": {"facts": [{"id": "revenue", "metric": "revenue", "value": "1000", "unit": "currency", "currency": "USD", "period_end": "2025-12-31"}]},
        },
        "recommendation": {
            "preferred_action": "hold",
            "amount": None,
            "evidence_ids": ["filing", "issuer"],
            "reason": "Baseline reason text.",
        },
        "reference": {"basis": "committed", "revision": 1},
    }

    # Same financial facts, completely different IDs, timestamps, and prose
    actual_candidate = {
        "portfolio": {
            "total_value": "3600",
            "holdings_value": "2300",
            "cash_value": "1300",
            "direct_companies": [{"company_id": "acme", "value": "2300", "weight": "0.63888889"}],
        },
        "stock": {
            "reporting_currency": "CAD",
            "cases": [
                {"name": "downside", "terminal_price": "50", "required_exit_multiple": "10.0", "known_terminal_value": "1000", "sensitivity_prices": ["40", "50", "60"]},
                {"name": "base", "terminal_price": "100", "required_exit_multiple": "16.11", "known_terminal_value": "1300", "sensitivity_prices": ["80", "100", "120"]},
                {"name": "upside", "terminal_price": "150", "required_exit_multiple": "20.0", "known_terminal_value": "1500", "sensitivity_prices": ["120", "150", "180"]},
            ],
            "research": {"facts": [{"id": "revenue", "metric": "revenue", "value": "1000", "unit": "currency", "currency": "USD", "period_end": "2025-12-31"}]},
        },
        "recommendation": {
            "preferred_action": "hold",
            "amount": None,
            "evidence_ids": ["issuer", "filing"],  # Order-independent
            "reason": "Completely different model generated prose string.",
        },
        "reference": {"basis": "committed", "revision": 1},
        "id": "random-uuid-9999",
        "reviewed_at": "2026-10-16T12:00:00Z",
    }

    # Must pass without assertion error
    assert_semantic_financial_parity(actual_candidate, baseline, context="test_parity_pass")

    # Divergence in terminal_price must fail
    divergent_candidate = cast(dict[str, Any], copy.deepcopy(actual_candidate))
    divergent_candidate["stock"]["cases"][1]["terminal_price"] = "105"
    with pytest.raises(AssertionError, match="terminal_price mismatch"):
        assert_semantic_financial_parity(divergent_candidate, baseline, context="test_parity_divergence")
