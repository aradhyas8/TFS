"""Ticket 07 workflow adapters at the approved FastAPI seam."""

import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.config import Settings
from analyst.financial_data import FakeFinancialProvider
from analyst.portfolio import PortfolioStore
from analyst.providers import FakeDataProvider, ModelTurn, ToolCall
from analyst.research import ReviewedResearchProvider
from analyst.schemas import AnalysisRequest, CompanyResearch, Identity, SavedPortfolio
from tests.test_allocation import (
    allocation_answer,
    allocation_evidence,
    allocation_request,
    comparison_judgments,
)
from tests.test_analysis import recommendation, snapshot
from tests.test_context_retrieval import CapturingTransport, submit
from tests.test_financial_tools import NoWorkflowData
from tests.test_stock import (
    company_judgments,
    research_fixture,
    stock_answer,
    stock_comparison_judgments,
    stock_request,
)
from tests.test_theme import theme_request, theme_turns


def call(name, arguments=None, id=None):
    return ModelTurn(calls=[ToolCall(id or name, name, json.dumps(arguments or {}))])


WORKFLOW_NAMES = (
    "run_exposure_review", "run_stock_analysis", "run_new_cash_allocation",
    "run_portfolio_reunderwriting", "run_theme_exploration",
)


@pytest.mark.parametrize("selected,called", [
    (selected, called) for selected in WORKFLOW_NAMES for called in WORKFLOW_NAMES if selected != called
])
def test_explicit_workflow_rejects_conflicting_adapter_before_pipeline_execution(tmp_path, selected, called):
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))
    arguments = {"position_id": "p1"} if called == "run_stock_analysis" else {}
    model = CapturingTransport([call(called, arguments), ModelTurn(text="Use the selected workflow.")])
    with TestClient(create_app(model=model, data=NoWorkflowData(), portfolio_store=store,
                              settings=Settings(api_key="sk-test", model="test"))) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Run the selected workflow.", workflow_input={"workflow": selected})
        assert run["status"] == "completed", run
        assert run["artifact_ids"] == []
        messages = client.get(f"/api/threads/{thread}").json()["messages"]
        outcome = messages[1]["tool_outcomes"][0]
        assert outcome["name"] == called
        assert outcome["status"] == "error"
        assert outcome["error"] == "Requested workflow conflicts with the explicit workflow selection."
        assert len(model.requests) == 2


def workflow_case(name):
    if name == "run_stock_analysis":
        request = stock_request()
        bound = AnalysisRequest.model_validate(request).model_dump(mode="json")
        turns = [call("review_portfolio"), call("get_sec_filings"), call("get_issuer_material"),
                 call("calculate_company_cases", company_judgments()),
                 call("calculate_comparison", stock_comparison_judgments(bound)), ModelTurn(answer=stock_answer())]
    elif name == "run_theme_exploration":
        request = theme_request(True)
        turns = theme_turns(request)
    elif name == "run_new_cash_allocation":
        request = allocation_request()
        turns = [call("review_portfolio"), call("scan_opportunities"), call("calculate_comparison", comparison_judgments()),
                 call("size_allocation", {"position_id": "fund", "min_weight": "0.55", "max_weight": "0.65",
                                          "reason": "Diversification with a cash reserve."}), ModelTurn(answer=allocation_answer())]
    else:
        request = {"question": "Review cash.", "portfolio": snapshot(), "portfolio_review": {}}
        request["portfolio"]["positions"] = [request["portfolio"]["positions"][2]]
        bound = AnalysisRequest.model_validate(request).model_dump(mode="json")
        turns = [call("review_portfolio"), call("calculate_comparison", stock_comparison_judgments(bound)),
                 ModelTurn(answer={**recommendation(), "preferred_action": "no_action", "evidence_ids": []})]
    return request, turns


@pytest.mark.parametrize("name,result_field,input_field", [
    ("run_stock_analysis", "stock", "stock"), ("run_new_cash_allocation", "allocation", "new_cash"),
    ("run_portfolio_reunderwriting", "reunderwriting", "portfolio_review"), ("run_theme_exploration", "theme", "theme"),
])
def test_complete_workflows_preserve_typed_results_and_explicit_inputs(tmp_path, name, result_field, input_field):
    request, turns = workflow_case(name)
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    saved = SavedPortfolio.model_validate({"snapshot": request["portfolio"], "settings": request.get("settings")})
    store.save(saved)
    inputs = {key: value for key, value in request.items() if key not in {"question", "portfolio", "settings"}}
    inputs.update(workflow=name, analysis_date="2026-09-30")
    model = CapturingTransport([call(name), *turns, ModelTurn(text="Done.")])
    with TestClient(create_app(model=model, data=FakeDataProvider(),
                              financial=FakeFinancialProvider(allocation_evidence()) if result_field == "allocation" else FakeFinancialProvider(),
                              research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research_fixture())}),
                              portfolio_store=store, settings=Settings(api_key="sk-test", model="test", request_timeout=5))) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, request["question"], workflow_input=inputs)
        assert run["status"] == "completed", run
        assert json.loads(model.requests[0]["messages"][1]["content"])["explicit_workflow"] == name
        assert len(run["artifact_ids"]) == 1, client.get(f"/api/threads/{thread}").json()
        artifact = client.get(f"/api/artifacts/{run['artifact_ids'][0]}").json()
        assert artifact["payload"][result_field] is not None
        assert artifact["payload"]["question"] == request["question"]
        assert artifact["payload"]["portfolio"]["holdings_as_of"] == "2026-09-30"
        nested_request = json.loads(model.requests[1]["messages"][1]["content"])
        assert nested_request["settings"] == (saved.settings.model_dump(mode="json") if saved.settings else None)
        for key, value in request[input_field].items():
            assert nested_request[input_field][key] == value
        assert client.get("/api/portfolio").json()["revision"] == 1
    # The chat model sees the result itself, within the budget and without anything cut to fit.
    chat = model.requests[-1]["messages"]
    output = json.loads(next(m["output"] for m in chat if m.get("type") == "function_call_output"))
    assert "error" not in output and "omitted to fit" not in output.get("retrieval", "")
    assert output["payload"]["recommendation"] == artifact["payload"]["recommendation"]
    assert output["payload"][result_field] is not None
    assert sum(len(m["content"]) if "content" in m else len(json.dumps(m)) for m in chat[2:]) <= 32000
    if name == "run_stock_analysis":
        assert output["payload"]["stock"]["valuation"] == artifact["payload"]["stock"]["valuation"]


@pytest.mark.parametrize("name,field", [("run_new_cash_allocation", "new_cash"), ("run_theme_exploration", "theme")])
def test_agreement_requires_matching_form_and_resumes_only_in_new_run(tmp_path, name, field):
    request, turns = workflow_case(name)
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": request["portfolio"], "settings": request.get("settings")}))
    inputs = {key: value for key, value in request.items() if key not in {"question", "portfolio", "settings"}}
    inputs["analysis_date"] = "2026-09-30"
    model = CapturingTransport([call(name), ModelTurn(text="Use the agreement form."), call(name), *turns, ModelTurn(text="Done.")])
    with TestClient(create_app(model=model, data=FakeDataProvider(), financial=FakeFinancialProvider(allocation_evidence()),
                              research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research_fixture())}),
                              portfolio_store=store, settings=Settings(api_key="sk-test", model="test", request_timeout=5))) as client:
        thread = client.post("/api/threads").json()["id"]
        first = submit(client, thread, "Yes I agree.", workflow_input=inputs)
        assert first["artifact_ids"] == []
        clarification = client.get(f"/api/threads/{thread}").json()["messages"][1]["clarification_required"]
        assert clarification["fields"] == [f"{field}.confirmed"]
        second = submit(client, thread, request["question"], "req-2", workflow_input={**inputs, "workflow": name})
        assert second["status"] == "completed", second
        assert second["id"] != first["id"]
        assert len(second["artifact_ids"]) == 1
        assert client.get(f"/api/threads/{thread}/runs/{first['id']}").json()["artifact_ids"] == []


@pytest.mark.parametrize("arguments", [{"confirmed": True}, {"new_cash": {"confirmed": True}}, {"portfolio": {}}])
def test_model_cannot_forge_agreement_or_authoritative_workflow_state(tmp_path, arguments):
    model = CapturingTransport([call("run_new_cash_allocation", arguments)])
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))
    with TestClient(create_app(model=model, portfolio_store=store, settings=Settings(api_key="sk-test", model="test"))) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Go ahead.")
        assert run["status"] == "failed"
        assert run["error"] == "Invalid application tool arguments."
        assert run["artifact_ids"] == []
        assert len(model.requests) == 1


def test_nested_financial_requests_use_configured_timeout(tmp_path):
    class SlowFinancial(FakeFinancialProvider):
        async def identity(self, position, as_of):
            await asyncio.sleep(0.08)
            return await super().identity(position, as_of)

    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))
    model = CapturingTransport([call("run_exposure_review"), call("review_portfolio"),
                                ModelTurn(answer=recommendation()), ModelTurn(text="Done.")])
    with TestClient(create_app(model=model, financial=SlowFinancial(), portfolio_store=store,
                              settings=Settings(api_key="sk-test", model="test", request_timeout=0.03))) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Review exposure.")
        assert run["artifact_ids"] == []
        messages = client.get(f"/api/threads/{thread}").json()["messages"]
        assert messages[1]["tool_outcomes"][0]["status"] == "error"
        assert "timed out" in messages[1]["tool_outcomes"][0]["error"]


@pytest.mark.parametrize("operation", ["model", "financial", "research"])
def test_cancellation_closes_nested_work_without_publishing_results(tmp_path, operation):
    entered, stopped = threading.Event(), threading.Event()

    async def block():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    class BlockingModel(CapturingTransport):
        async def respond(self, messages, **kwargs):
            if operation == "model" and kwargs.get("output_schema") is not None:
                await block()
            return await super().respond(messages, **kwargs)

    class BlockingFinancial(FakeFinancialProvider):
        async def identity(self, position, as_of):
            if operation == "financial":
                await block()
            return await super().identity(position, as_of)

    class BlockingResearch(ReviewedResearchProvider):
        async def company(self, position, as_of):
            await block()

    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))
    name = "run_stock_analysis" if operation == "research" else "run_exposure_review"
    model = BlockingModel([call(name, {"position_id": "p1"} if operation == "research" else {}),
                           call("review_portfolio"), call("get_sec_filings")])
    with TestClient(create_app(model=model, financial=BlockingFinancial(), research=BlockingResearch(),
                              portfolio_store=store, settings=Settings(api_key="sk-test", model="test"))) as client:
        thread = client.post("/api/threads").json()["id"]
        response = client.post(f"/api/threads/{thread}/messages", json={"text": "Review.", "client_request_id": "cancel"})
        assert response.status_code == 202
        assert entered.wait(2)
        run_id = response.json()["id"]
        assert client.post(f"/api/threads/{thread}/runs/{run_id}/cancel").json()["status"] == "cancelled"
        assert stopped.wait(2)
        run = client.get(f"/api/threads/{thread}/runs/{run_id}").json()
        assert run["status"] == "cancelled"
        assert run["artifact_ids"] == []


def test_late_nested_answer_cannot_publish_after_cancellation(tmp_path):
    entered, returned = threading.Event(), threading.Event()

    class LateModel(CapturingTransport):
        async def respond(self, messages, **kwargs):
            if len(self.requests) == 2:
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    returned.set()
                    return ModelTurn(answer=recommendation())
            return await super().respond(messages, **kwargs)

    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))
    model = LateModel([call("run_exposure_review"), call("review_portfolio"), ModelTurn(text="Late.")])
    app = create_app(model=model, financial=FakeFinancialProvider(), portfolio_store=store,
                     settings=Settings(api_key="sk-test", model="test"))
    with TestClient(app) as client:
        thread = client.post("/api/threads").json()["id"]
        response = client.post(f"/api/threads/{thread}/messages", json={"text": "Review.", "client_request_id": "cancel"})
        assert entered.wait(2)
        run_id = response.json()["id"]
        client.post(f"/api/threads/{thread}/runs/{run_id}/cancel")
        assert returned.wait(2)
        async def finished():
            await asyncio.sleep(0)
        client.portal.call(finished)
        assert client.get(f"/api/threads/{thread}/runs/{run_id}").json()["artifact_ids"] == []
        assert len(client.get(f"/api/threads/{thread}").json()["messages"]) == 1


@pytest.mark.parametrize("count,status", [(2, "completed"), (8, "failed")])
def test_multiple_workflows_and_direct_tools_share_outer_turn_budget(tmp_path, count, status):
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))
    turns = [] if count == 8 else [call("review_portfolio", id="direct")]
    for index in range(count):
        turns.extend([call("run_exposure_review", id=f"workflow-{index}"), call("review_portfolio"),
                      ModelTurn(answer=recommendation())])
    model = CapturingTransport([*turns, ModelTurn(text="Done.")])
    with TestClient(create_app(model=model, financial=FakeFinancialProvider(), portfolio_store=store,
                              settings=Settings(api_key="sk-test", model="test", request_timeout=5))) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Explain concentration and new cash concepts while reviewing exposure.",
                     workflow_input={"analysis_date": "2026-09-30"})
        assert run["status"] == status, run
        kinds = [client.get(f"/api/artifacts/{id}").json()["reference"]["kind"] for id in run["artifact_ids"]]
        assert kinds.count("analysis_result") == count
        if count == 8:
            assert "Maximum orchestration turns (8)" in run["error"]
        else:
            assert kinds[0] == "calculation_result"


def test_outer_deadline_cancels_nested_model_and_caps_request_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr("analyst.orchestrator.MAX_DEADLINE_SECONDS", 0.1)
    stopped = threading.Event()

    class DeadlineModel(CapturingTransport):
        async def respond(self, messages, **kwargs):
            if kwargs.get("output_schema") is not None:
                assert 0 < kwargs["timeout"] <= 0.1
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()
            return await super().respond(messages, **kwargs)

    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))
    with TestClient(create_app(model=DeadlineModel([call("run_exposure_review")]), financial=FakeFinancialProvider(),
                              portfolio_store=store, settings=Settings(api_key="sk-test", model="test", request_timeout=5))) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Review exposure.")
        assert stopped.wait(1)
        assert run["status"] == "failed"
        assert "deadline" in run["error"]
        assert run["artifact_ids"] == []


def test_new_cash_binds_zero_balance_destination_and_saved_benchmark(tmp_path):
    request, turns = workflow_case("run_new_cash_allocation")
    request["new_cash"].pop("cash_position_id")
    request["new_cash"].update(account_id="broker", currency="USD")
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": request["portfolio"], "settings": request["settings"],
        "benchmark": {"ticker": "BROAD", "listing": "XNYS", "currency": "USD"}}))
    # A premature recommendation exercises the real pipeline rejection after request construction.
    model = CapturingTransport([call("run_new_cash_allocation"), ModelTurn(answer=recommendation())])
    with TestClient(create_app(model=model, financial=FakeFinancialProvider(allocation_evidence()),
                              portfolio_store=store, settings=Settings(api_key="sk-test", model="test"))) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, request["question"], workflow_input={"workflow": "run_new_cash_allocation",
                     "new_cash": request["new_cash"], "analysis_date": "2026-09-30"})
        assert run["status"] == "failed"
        nested = json.loads(model.requests[1]["messages"][1]["content"])
        rows = {row["id"]: row for row in nested["portfolio"]["positions"]}
        assert rows["new-cash-destination"]["cash"] == "0"
        assert rows["new-cash-destination"]["account_id"] == "broker"
        assert rows["benchmark-fund"]["shares"] == "0"
        assert rows["benchmark-fund"]["etf_role"] == "diversified"
        assert nested["new_cash"]["cash_position_id"] == "new-cash-destination"
        committed_rows = client.get("/api/portfolio").json()["portfolio"]["snapshot"]["positions"]
        assert [row["id"] for row in committed_rows] == ["fund", "cash"]
        assert committed_rows[1]["cash"] == "2000"


def test_resolved_candidate_is_available_to_stock_workflow_without_becoming_held(tmp_path):
    class CandidateFinancial(FakeFinancialProvider):
        async def lookup(self, *args):
            return [Identity(status="supplied", ticker="ACME", listing="XNAS", currency="USD", kind="stock",
                             company_id="acme", company_name="Acme", source="Reviewed fixture")]

    class CandidateModel(CapturingTransport):
        candidate_id = None

        async def respond(self, messages, **kwargs):
            if len(self.requests) == 1:
                candidate = json.loads(messages[-1]["output"])["payload"]["result"]["position"]
                self.candidate_id = candidate["id"]
                request = {"question": "Research Acme.", "portfolio": snapshot(), "stock": {"position_id": self.candidate_id}}
                request["portfolio"]["positions"].append(candidate)
                bound = AnalysisRequest.model_validate(request).model_dump(mode="json")
                self.turns = iter([call("run_stock_analysis", {"position_id": self.candidate_id}),
                    call("review_portfolio"), call("get_sec_filings"), call("get_issuer_material"),
                    call("calculate_company_cases", company_judgments()),
                    call("calculate_comparison", stock_comparison_judgments(bound)),
                    ModelTurn(answer=stock_answer()), ModelTurn(text="Done.")])
            return await super().respond(messages, **kwargs)

    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))
    model = CandidateModel([call("resolve_candidate", {"ticker": "ACME", "listing": "XNAS", "account_id": "tfsa"})])
    with TestClient(create_app(model=model, financial=CandidateFinancial(),
                              research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research_fixture())}),
                              portfolio_store=store, settings=Settings(api_key="sk-test", model="test", request_timeout=5))) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Research the unowned Acme listing.", workflow_input={"analysis_date": "2026-09-30"})
        assert run["status"] == "completed", run
        artifact = client.get(f"/api/artifacts/{run['artifact_ids'][-1]}").json()
        assert artifact["payload"]["stock"]["position_id"] == model.candidate_id
        candidate_row = next(row for row in artifact["payload"]["portfolio"]["positions"] if row["supplied"]["id"] == model.candidate_id)
        assert candidate_row["supplied"]["shares"] == "0"
        assert all(row["id"] != model.candidate_id for row in client.get("/api/portfolio").json()["portfolio"]["snapshot"]["positions"])


@pytest.mark.parametrize("bad_input", [{"portfolio": {}}, {"settings": {}}, {"benchmark": {}}, {"theme": {"confirmed": True}}])
def test_invalid_explicit_inputs_are_rejected_at_acceptance(tmp_path, bad_input):
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    model = CapturingTransport([])
    with TestClient(create_app(model=model, portfolio_store=store, settings=Settings(api_key="sk-test", model="test"))) as client:
        thread = client.post("/api/threads").json()["id"]
        response = client.post(f"/api/threads/{thread}/messages", json={"text": "Review.", "client_request_id": "bad", "workflow_input": bad_input})
        assert response.status_code == 422
        assert model.requests == []
        assert client.get(f"/api/threads/{thread}").json()["runs"] == []


@pytest.mark.parametrize("tool,fields,form", [
    ("run_stock_analysis", ["stock.position_id"], "/stock"),
    ("run_new_cash_allocation", ["new_cash.amount", "new_cash.account_id", "new_cash.risk_context", "new_cash.confirmed"], "/new-cash"),
    ("run_theme_exploration", ["theme.name", "theme.mechanism", "theme.shortlist", "theme.confirmed"], "/theme"),
])
def test_missing_workflow_inputs_are_persisted_as_typed_form_requirements(tmp_path, tool, fields, form):
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))
    model = CapturingTransport([ModelTurn(calls=[ToolCall("workflow", tool, "{}")]), ModelTurn(text="Use the form.")])
    with TestClient(create_app(model=model, data=FakeDataProvider(), financial=FakeFinancialProvider(),
                              portfolio_store=store, settings=Settings(api_key="sk-test", model="test"))) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Yes, I agree. Please run it.")
        assert run["status"] == "completed", run
        messages = client.get(f"/api/threads/{thread}").json()["messages"]
        clarification = next(m["clarification_required"] for m in messages if m["clarification_required"])
        assert clarification["fields"] == fields
        assert clarification["form"] == form
        assert clarification["workflow"] == tool
        assert run["artifact_ids"] == []
        assert len(model.requests) == 2


@pytest.mark.parametrize("workflow", [None, "run_exposure_review"])
def test_exposure_workflow_stores_original_result_and_bound_revision(tmp_path, workflow):
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot(), "average_costs": {"p1": "12.34"}}))
    model = CapturingTransport([
        ModelTurn(calls=[ToolCall("workflow", "run_exposure_review", "{}")]),
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        ModelTurn(answer=recommendation()),
        ModelTurn(text="Your review is available."),
    ])
    with TestClient(create_app(model=model, data=FakeDataProvider(), financial=FakeFinancialProvider(),
                              portfolio_store=store, settings=Settings(api_key="sk-test", model="test", request_timeout=5))) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Review my exposure.", workflow_input={
            "analysis_date": "2026-09-30", "workflow": workflow,
        })
        assert run["status"] == "completed", run
        artifact = client.get(f"/api/artifacts/{run['artifact_ids'][0]}").json()
        assert artifact["reference"]["kind"] == "analysis_result"
        assert artifact["reference"]["revision"] == 1
        assert artifact["reference"]["originating_run_id"] == run["id"]
        assert artifact["payload"]["portfolio"]["total_value"] == "3600"
        assert artifact["payload"]["recommendation"] == recommendation()
        assert "12.34" not in json.dumps(model.requests)


def test_conflicting_workflow_call_can_recover_with_the_explicitly_selected_workflow(tmp_path):
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(SavedPortfolio.model_validate({"snapshot": snapshot()}))
    model = CapturingTransport([call("run_portfolio_reunderwriting"), call("run_exposure_review"),
                                call("review_portfolio"), ModelTurn(answer=recommendation()),
                                ModelTurn(text="The selected exposure review is ready.")])
    with TestClient(create_app(model=model, financial=FakeFinancialProvider(),
                              portfolio_store=store, settings=Settings(api_key="sk-test", model="test", request_timeout=5))) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Review my exposure.", workflow_input={
            "workflow": "run_exposure_review", "analysis_date": "2026-09-30",
        })
        assert run["status"] == "completed", run
        assert len(run["artifact_ids"]) == 1
        artifact = client.get(f"/api/artifacts/{run['artifact_ids'][0]}").json()
        assert artifact["payload"]["portfolio"]["total_value"] == "3600"
        assert artifact["payload"]["stock"] is None
        assert artifact["payload"]["reunderwriting"] is None
        outcomes = [outcome for message in client.get(f"/api/threads/{thread}").json()["messages"]
                    for outcome in message["tool_outcomes"]]
        assert [(outcome["name"], outcome["status"]) for outcome in outcomes] == [
            ("run_portfolio_reunderwriting", "error"), ("run_exposure_review", "success"),
        ]
