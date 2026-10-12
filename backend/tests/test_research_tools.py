"""Direct research and scenarios at the approved FastAPI seam."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.config import Settings
from analyst.financial_data import FakeFinancialProvider
from analyst.portfolio import PortfolioStore
from analyst.providers import ModelTurn, ToolCall
from analyst.research import ReviewedResearchProvider
from analyst.schemas import CompanyResearch, DiscoveryCandidate, DiscoveryScan
from tests.test_canadian_stock import canadian_research_fixture
from tests.test_comparison import driver
from tests.test_context_retrieval import CapturingTransport, submit
from tests.test_financial_tools import NoWorkflowData, evidence, portfolio
from tests.test_stock import company_judgments, research_fixture


def run_tools(tmp_path, calls, *, saved=True, record=None, discovery=None, canadian=False, model=None):
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    holding = portfolio()
    holding.snapshot.positions[0].listing = "XNAS"
    holding.snapshot.positions[0].currency = "USD"
    holding.snapshot.reporting_currency = "USD"
    holding.snapshot.positions[1].currency = "USD"
    facts = evidence()
    facts.identities["stock"].listing = "XNAS"
    facts.identities["stock"].currency = "USD"
    facts.quotes["stock"].listing = "XNAS"
    facts.quotes["stock"].currency = "USD"
    if canadian:
        holding.snapshot.positions[0].listing = "XTSE"
        holding.snapshot.positions[0].currency = "CAD"
        facts.identities["stock"].listing = "XTSE"
        facts.identities["stock"].currency = "CAD"
        facts.quotes["stock"].listing = "XTSE"
        facts.quotes["stock"].currency = "CAD"
        holding.snapshot.reporting_currency = "CAD"
        holding.snapshot.positions[1].currency = "CAD"
    if saved:
        store.save(holding)
    model = model or CapturingTransport([
        *[ModelTurn(calls=[ToolCall(str(i), name, json.dumps(args))]) for i, (name, args) in enumerate(calls)],
        ModelTurn(text="Done."),
    ])
    with TestClient(create_app(
        model=model, data=NoWorkflowData(), financial=FakeFinancialProvider(facts),
        research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(record or research_fixture())}),
        discovery=discovery, portfolio_store=store,
        settings=Settings(api_key="sk-test", model="test-model", request_timeout=5),
    )) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Retrieve primary issuer evidence.")
        artifacts = [client.get(f"/api/artifacts/{id}").json() for id in run["artifact_ids"]]
        current = client.get("/api/portfolio").json()
        if saved:
            assert current["revision"] == 1
            assert current["portfolio"]["snapshot"] == holding.snapshot.model_dump(mode="json")
    return run, artifacts, model


def test_direct_primary_research_retains_sources_and_run_basis(tmp_path):
    run, artifacts, model = run_tools(tmp_path, [("get_sec_filings", {"position_id": "stock"})])
    assert run["status"] == "completed"
    artifact = artifacts[0]
    assert artifact["reference"]["kind"] == "company_research"
    assert artifact["reference"]["originating_run_id"] == run["id"]
    assert artifact["reference"]["basis"] == "committed"
    assert artifact["payload"]["position_id"] == "stock"
    research = artifact["payload"]["research"]
    assert artifact["payload"]["document_ids"] == ["filing"]
    assert research["facts"][0]["document_ids"] == ["filing", "issuer"]
    assert artifact["payload"]["target"]["listing"] == "XNAS"
    assert research["documents"][0]["url"].startswith("https://www.sec.gov/")
    assert research["documents"][0]["published_on"] == "2026-02-01"
    assert "12.34" not in json.dumps(model.requests)


def test_research_and_scenario_tool_schemas_are_strict_at_transport_boundary(tmp_path):
    _, _, model = run_tools(tmp_path, [("get_sec_filings", {"position_id": "stock"})])

    def check(schema):
        if isinstance(schema, dict):
            if schema.get("type") == "object":
                assert set(schema["required"]) == set(schema["properties"])
                assert schema["additionalProperties"] is False
            assert "default" not in schema
            for value in schema.values():
                check(value)
        elif isinstance(schema, list):
            for value in schema:
                check(value)

    for tool in model.requests[0]["tools"]:
        check(tool["parameters"])


def test_direct_company_cases_use_reported_facts_and_real_calculations(tmp_path):
    run, artifacts, _ = run_tools(tmp_path, [
        ("get_sec_filings", {"position_id": "stock"}),
        ("get_issuer_material", {"position_id": "stock"}),
        ("calculate_company_cases", {"position_id": "stock", "judgments": company_judgments()}),
    ])
    assert run["status"] == "completed"
    result = artifacts[-1]["payload"]["result"]
    assert artifacts[-1]["payload"]["capability"] == "calculate_company_cases"
    assert [case["terminal_price"] for case in result["cases"]] == ["50", "100", "150"]
    assert result["cases"][1]["known_terminal_value"] == "1000"
    assert {doc["id"] for doc in result["research"]["documents"]} == {"filing", "issuer"}


def comparison_args():
    return {
        "scope_position_ids": ["cash"],
        "alternatives": [{"id": "cash", "kind": "cash", "position_id": "cash"},
                         {"id": "keep", "kind": "no_action", "position_id": None}],
        "judgments": {"alternatives": [{"alternative_id": id, "cases": [
            {"name": name, "drivers": [driver("cash", cash=True)],
             "assumptions": ["Rates may moderate."], "downside": "Falling rates reduce income.",
             "uncertainty": ["Future rates are unknown."]}
            for name in ("downside", "base", "upside")]} for id in ("cash", "keep")]},
    }


def test_direct_comparison_keeps_common_capital_and_unknown_effects(tmp_path):
    run, artifacts, _ = run_tools(tmp_path, [("calculate_comparison", comparison_args())])
    assert run["status"] == "completed"
    result = artifacts[0]["payload"]["result"]
    assert artifacts[0]["reference"]["basis"] == "hypothetical"
    assert result["starting_value"] == "800"
    for alternative in result["alternatives"]:
        case = alternative["cases"][1]
        assert case["known_terminal_value"] == "882.840192"
        assert case["terminal_value"] is None
        assert "taxes" in case["unmodeled"]


def test_direct_screen_is_bounded_and_does_not_add_holdings(tmp_path):
    from analyst.allocation import ReviewedDiscoveryProvider
    run, artifacts, _ = run_tools(tmp_path, [("scan_opportunities", {})], discovery=ReviewedDiscoveryProvider())
    assert run["status"] == "completed"
    result = artifacts[0]["payload"]["result"]
    assert artifacts[0]["payload"]["capability"] == "scan_opportunities"
    assert 1 <= len(result["candidates"]) <= 8
    assert result["candidates"][0]["position"]["shares"] == "0"
    assert "limited to supplied holdings" in str(result["issues"])


@pytest.mark.parametrize("name,canadian", [("get_sec_filings", True), ("get_sedar_filings", False)])
def test_primary_jurisdiction_mismatch_requests_correct_route(tmp_path, name, canadian):
    run, artifacts, _ = run_tools(tmp_path, [(name, {"position_id": "stock"})], canadian=canadian)
    assert run["status"] == "completed"
    assert artifacts[0]["payload"]["clarification_required"]["code"] == "unavailable"


@pytest.mark.parametrize("fault", ["missing", "future", "wrong_issuer"])
def test_primary_unavailable_or_conflicting_evidence_stays_qualified(tmp_path, fault):
    record = research_fixture()
    if fault == "missing":
        record.update(documents=[], facts=[])
    elif fault == "future":
        record["documents"][0]["published_on"] = "2099-01-01"
    else:
        record["company_id"] = "other"
        for doc in record["documents"]:
            doc["company_id"] = "other"
    run, artifacts, _ = run_tools(tmp_path, [("get_sec_filings", {"position_id": "stock"})], record=record)
    assert run["status"] == "completed"
    payload = artifacts[0]["payload"]
    if fault == "wrong_issuer":
        assert payload["clarification_required"]
    else:
        assert not any(doc["available"] for doc in payload["research"]["documents"] if doc["id"] in payload["document_ids"])
        assert payload["research"]["issues"]


def test_cases_require_primary_retrieval(tmp_path):
    run, artifacts, _ = run_tools(tmp_path, [("calculate_company_cases", {
        "position_id": "stock", "judgments": company_judgments(),
    })])
    assert run["status"] == "completed"
    assert "Retrieve" in artifacts[0]["payload"]["clarification_required"]["message"]


@pytest.mark.parametrize("name,args", [
    ("get_sec_filings", {"position_id": "stock", "url": "https://example.com"}),
    ("calculate_company_cases", {"position_id": "stock", "judgments": company_judgments(), "facts": []}),
    ("calculate_comparison", {**comparison_args(), "effects": []}),
    ("calculate_comparison", {**comparison_args(), "fund_facts": []}),
    ("scan_opportunities", {"candidates": []}),
])
def test_model_cannot_replace_financial_or_research_facts(tmp_path, name, args):
    run, artifacts, _ = run_tools(tmp_path, [(name, args)])
    assert run["status"] == "failed" and run["error"] == "Invalid application tool arguments."
    assert artifacts == []


@pytest.mark.parametrize("fault", ["omitted_alternative", "wrong_driver", "quantitative_prose"])
def test_comparison_rejects_incomplete_or_unsupported_judgments(tmp_path, fault):
    args = comparison_args()
    if fault == "omitted_alternative":
        args["judgments"]["alternatives"].pop()
    elif fault == "wrong_driver":
        args["judgments"]["alternatives"][0]["cases"][0]["drivers"][0]["position_id"] = "stock"
    else:
        args["judgments"]["alternatives"][0]["cases"][0]["assumptions"] = ["Returns are guaranteed at 10%."]
    run, artifacts, model = run_tools(tmp_path, [("calculate_comparison", args)])
    assert run["status"] == "completed"
    assert artifacts == []
    assert "error" in json.loads(model.requests[-1]["messages"][-1]["output"])


@pytest.mark.parametrize("saved", [True, False])
def test_unowned_primary_research_uses_temporary_identity_without_portfolio_mutation(tmp_path, saved):
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    if saved:
        store.save(portfolio())

    class ResolveThenResearch(CapturingTransport):
        candidate_id = None

        async def respond(self, messages, **kwargs):
            self.requests.append({"messages": messages, **kwargs})
            if len(self.requests) == 1:
                name, args = "resolve_candidate", {"ticker": "ACME", "listing": "XTSE", "account_id": None}
            elif len(self.requests) == 2:
                candidate = json.loads(messages[-1]["output"])["payload"]["result"]["position"]
                assert candidate["shares"] == "0"
                self.candidate_id = candidate["id"]
                name, args = "get_issuer_material", {"position_id": candidate["id"]}
            elif saved and len(self.requests) == 3:
                name, args = "get_sedar_filings", {"position_id": self.candidate_id}
            elif saved and len(self.requests) == 4:
                name, args = "calculate_company_cases", {"position_id": self.candidate_id, "judgments": company_judgments()}
            else:
                return ModelTurn(text="The issuer material is available.")
            return ModelTurn(calls=[ToolCall(str(len(self.requests)), name, json.dumps(args))])

    model = ResolveThenResearch([])
    with TestClient(create_app(
        model=model, data=NoWorkflowData(), portfolio_store=store,
        financial=FakeFinancialProvider(evidence()),
        research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(canadian_research_fixture())}),
        settings=Settings(api_key="sk-test", model="test-model", request_timeout=5),
    )) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Retrieve an unowned issuer's release.")
        assert run["status"] == "completed"
        artifact = client.get(f"/api/artifacts/{run['artifact_ids'][-1]}").json()
        assert artifact["reference"]["basis"] == ("hypothetical" if saved else "none")
        assert artifact["reference"]["originating_run_id"] == run["id"]
        if saved:
            assert artifact["payload"]["result"]["cases"][1]["terminal_price"] == "100"
            assert artifact["payload"]["result"]["cases"][1]["known_terminal_value"] == "0"
        else:
            assert artifact["payload"]["document_ids"] == ["issuer_release"]
        current = client.get("/api/portfolio").json()
        if saved:
            assert current["revision"] == 1
            assert len(current["portfolio"]["snapshot"]["positions"]) == 2
        else:
            assert current is None


class ThreeCandidateScreen:
    async def scan(self, snapshot):
        stock = snapshot.positions[0]
        return DiscoveryScan(scanned_at=datetime.now(UTC), as_of=snapshot.as_of, source="Reviewed fixture",
            candidates=[DiscoveryCandidate(
                position=stock.model_copy(update={"id": id, "shares": Decimal(0)}),
                as_of=snapshot.as_of, source="Reviewed fixture", signal="Conditional operating review.",
            ) for id in ("one", "two", "three")])


def test_screen_research_stops_after_two_candidates(tmp_path):
    run, artifacts, _ = run_tools(tmp_path, [
        ("scan_opportunities", {}),
        *[("get_sec_filings", {"position_id": id}) for id in ("one", "two", "three")],
    ], discovery=ThreeCandidateScreen())
    assert run["status"] == "completed"
    assert len([a for a in artifacts if a["reference"]["kind"] == "company_research"]) == 2
    assert "budget" in artifacts[-1]["payload"]["clarification_required"]["message"]


def test_resolving_a_screened_security_again_cannot_bypass_research_budget(tmp_path):
    class Alias(CapturingTransport):
        async def respond(self, messages, **kwargs):
            self.requests.append({"messages": messages, **kwargs})
            step = len(self.requests)
            if step == 1:
                name, args = "scan_opportunities", {}
            elif step in {2, 3}:
                name, args = "get_sec_filings", {"position_id": "one" if step == 2 else "two"}
            elif step == 4:
                name, args = "resolve_candidate", {"ticker": "ACME", "listing": "XNAS", "account_id": None}
            elif step == 5:
                position = json.loads(messages[-1]["output"])["payload"]["result"]["position"]
                name, args = "get_sec_filings", {"position_id": position["id"]}
            else:
                return ModelTurn(text="Done.")
            return ModelTurn(calls=[ToolCall(str(step), name, json.dumps(args))])
    run, artifacts, _ = run_tools(tmp_path, [], discovery=ThreeCandidateScreen(), model=Alias([]))
    assert run["status"] == "completed"
    assert "budget" in artifacts[-1]["payload"]["clarification_required"]["message"]


def test_stock_comparison_links_the_exact_company_case_artifact(tmp_path):
    args = comparison_args()
    args["alternatives"].insert(0, {"id": "company", "kind": "stock", "position_id": "stock"})
    stock_driver = driver("stock", reinvest=False)
    stock_driver.update(annual_returns=None, income_multipliers=None)
    args["judgments"]["alternatives"].insert(0, {"alternative_id": "company", "cases": [
        {"name": name, "drivers": [stock_driver], "assumptions": ["Conditional company cases apply."],
         "downside": "Operating losses impair capital.", "uncertainty": ["Valuation may differ."]}
        for name in ("downside", "base", "upside")]})
    run, artifacts, _ = run_tools(tmp_path, [
        ("get_sec_filings", {"position_id": "stock"}),
        ("get_issuer_material", {"position_id": "stock"}),
        ("calculate_company_cases", {"position_id": "stock", "judgments": company_judgments()}),
        ("calculate_comparison", args),
    ])
    assert run["status"] == "completed"
    assert artifacts[-1]["payload"]["source_artifact_ids"] == [artifacts[-2]["reference"]["id"]]
    company = artifacts[-1]["payload"]["result"]["alternatives"][0]
    assert company["cases"][1]["known_terminal_value"] == "4000"


@pytest.mark.parametrize("fault", ["too_many", "old", "duplicate", "conflicting"])
def test_screen_rejects_invalid_bounds_dates_and_identity(tmp_path, fault):
    class Screen:
        async def scan(self, snapshot):
            row = snapshot.positions[0].model_copy(update={"shares": Decimal(0)})
            candidate = {"position": row.model_dump(mode="json"), "as_of": snapshot.as_of.isoformat(),
                         "source": "Fixture", "signal": "Review operating evidence."}
            if fault == "conflicting":
                candidate["position"]["company_id"] = "other"
            return DiscoveryScan.model_validate({
                "scanned_at": datetime.now(UTC), "as_of": "2001-01-01" if fault == "old" else snapshot.as_of,
                "source": "Fixture", "candidates": [candidate] * (9 if fault == "too_many" else 2 if fault == "duplicate" else 1),
            })
    run, artifacts, _ = run_tools(tmp_path, [("scan_opportunities", {})], discovery=Screen())
    assert run["status"] == "completed"
    assert artifacts[0]["payload"]["clarification_required"]["code"] == "unavailable"


@pytest.mark.parametrize("fault", ["future", "unreviewed", "missing_fact"])
def test_company_cases_do_not_turn_unsupported_evidence_into_value(tmp_path, fault):
    record = research_fixture()
    if fault == "future":
        record["documents"][0]["published_on"] = "2099-01-01"
    elif fault == "unreviewed":
        record["facts"][0]["notes_checked"] = False
    else:
        record["facts"][0]["value"] = None
    run, artifacts, _ = run_tools(tmp_path, [
        ("get_sec_filings", {"position_id": "stock"}),
        ("get_issuer_material", {"position_id": "stock"}),
        ("calculate_company_cases", {"position_id": "stock", "judgments": company_judgments()}),
    ], record=record)
    assert run["status"] == "completed"
    result = artifacts[-1]["payload"]["result"]
    assert all(case["terminal_price"] is None for case in result["cases"])
    assert result["qualifications"]
