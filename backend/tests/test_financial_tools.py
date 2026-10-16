"""Ticket 05 direct capabilities through the FastAPI and fake-provider seams."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.config import Settings
from analyst.financial_data import MARKET_TIME, FakeFinancialProvider, QuoteCache
from analyst.portfolio import PortfolioStore
from analyst.providers import ModelTurn, ToolCall
from analyst.schemas import FinancialEvidence, SavedPortfolio
from tests.test_context_retrieval import CapturingTransport, submit


def portfolio() -> SavedPortfolio:
    today = datetime.now(MARKET_TIME).date()
    return SavedPortfolio.model_validate({
        "snapshot": {
            "as_of": (today - timedelta(days=10)).isoformat(), "reporting_currency": "CAD",
            "accounts": [{"id": "tfsa", "name": "TFSA"}],
            "positions": [
                {"id": "stock", "account_id": "tfsa", "kind": "stock", "currency": "CAD",
                 "ticker": "ACME", "listing": "XTSE", "shares": "10",
                 "company_id": "acme", "company_name": "Acme"},
                {"id": "cash", "account_id": "tfsa", "kind": "cash", "currency": "CAD", "cash": "800"},
            ],
        },
        "average_costs": {"stock": "12.34"},
        "settings": {"single_company_cap": "0.15", "active_budget": "0.9", "indirect_cap_policy": "direct_only"},
    })


def evidence() -> FinancialEvidence:
    today = datetime.now(MARKET_TIME).date().isoformat()
    return FinancialEvidence.model_validate({
        "identities": {"stock": {"status": "supplied", "ticker": "ACME", "listing": "XTSE",
                                  "currency": "CAD", "kind": "stock", "company_id": "acme",
                                  "company_name": "Acme", "source": "Fixture", "as_of": today}},
        "quotes": {"stock": {"ticker": "ACME", "listing": "XTSE", "currency": "CAD", "value": "20",
                             "as_of": today, "source": "Fixture", "status": "manual",
                             "captured_at": datetime.now(UTC).isoformat(), "basis": "unadjusted"}},
    })


class NoWorkflowData:
    def snapshot(self, supplied):
        raise AssertionError("Direct tools must not start a workflow")


def api(store: PortfolioStore, model: CapturingTransport, financial: FakeFinancialProvider) -> TestClient:
    return TestClient(create_app(
        model=model, data=NoWorkflowData(), financial=financial, portfolio_store=store,
        settings=Settings(api_key="sk-test", model="test-model", request_timeout=5),
    ))


def test_cash_and_exposure_use_bound_calculations_and_artifacts(tmp_path: Path) -> None:
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    saved = portfolio()
    store.save(saved)
    model = CapturingTransport([
        ModelTurn(calls=[ToolCall("exposure", "review_portfolio", "{}")]),
        ModelTurn(text="Cash is CAD 800 and Acme exceeds the saved cap."),
    ])
    with api(store, model, FakeFinancialProvider(evidence())) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "How much cash do I have and is Acme above my cap?")
        assert run["status"] == "completed"
        assert len(run["artifact_ids"]) == 1
        artifact = client.get(f"/api/artifacts/{run['artifact_ids'][0]}").json()
        assert artifact["reference"]["kind"] == "calculation_result"
        assert artifact["reference"]["revision"] == 1
        assert artifact["reference"]["basis"] == "committed"
        assert artifact["reference"]["originating_run_id"] == run["id"]
        assert artifact["payload"]["capability"] == "review_portfolio"
        result = artifact["payload"]["result"]
        assert result["cash_value"] == "800" and result["total_value"] == "1000"
        assert result["holdings_as_of"] == saved.snapshot.as_of.isoformat()
        assert result["as_of"] == datetime.now(MARKET_TIME).date().isoformat()
        assert result["guardrails"]["companies"][0]["status"] == "breached"
        assert client.get("/api/portfolio").json()["revision"] == 1
    output = json.loads(model.requests[-1]["messages"][-1]["output"])
    assert output == artifact
    assert len(model.requests) == 2
    assert "12.34" not in json.dumps(model.requests)


def tool_run(tmp_path, name, arguments, financial=None, saved=None):
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    expected = store.save(saved or portfolio()).model_dump(mode="json")
    model = CapturingTransport([
        ModelTurn(calls=[ToolCall("direct", name, json.dumps(arguments))]), ModelTurn(text="Done."),
    ])
    with api(store, model, financial or FakeFinancialProvider(evidence())) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Check the selected input.")
        artifacts = [client.get(f"/api/artifacts/{id}").json() for id in run["artifact_ids"]]
        assert client.get("/api/portfolio").json()["revision"] == 1
        assert client.get("/api/portfolio").json()["portfolio"] == expected
    return run, artifacts, model


@pytest.mark.parametrize("amount,status,cash", [("5", "blocked", "700"), ("100", "blocked", None)])
def test_hypothetical_checks_bind_funding_without_commits(tmp_path, amount, status, cash):
    run, artifacts, _ = tool_run(tmp_path, "check_proposed_changes", {"trades": [
        {"position_id": "stock", "shares_change": amount, "cash_position_id": "cash"},
    ]})
    assert run["status"] == "completed"
    assert artifacts[0]["reference"]["basis"] == "hypothetical"
    assert artifacts[0]["payload"]["capability"] == "check_proposed_changes"
    result = artifacts[0]["payload"]["result"]
    assert result["status"] == status and result["post_cash_value"] == cash
    assert result["changes"]["new_cash"] == []


@pytest.mark.parametrize("missing", ["quote", "fx", "stale", "identity"])
def test_unavailable_financial_inputs_remain_qualified(tmp_path, missing):
    saved, facts = portfolio(), evidence()
    if missing == "quote":
        facts.quotes.clear()
    elif missing == "fx":
        saved.snapshot.reporting_currency = "USD"
    elif missing == "stale":
        facts.quotes["stock"].as_of -= timedelta(days=20)
    else:
        facts.identities["stock"].status = "unresolved"
    run, artifacts, _ = tool_run(tmp_path, "review_portfolio", {}, FakeFinancialProvider(facts), saved)
    assert run["status"] == "completed"
    result = artifacts[0]["payload"]["result"]
    assert result["total_value"] is None
    assert result["qualifications"] and result["source_inputs_usable"] is False


def test_dated_evidence_rejects_future_quotes_and_keeps_holdings_date(tmp_path):
    run, artifacts, _ = tool_run(tmp_path, "get_financial_data", {
        "position_ids": ["stock"], "as_of": portfolio().snapshot.as_of.isoformat(),
    })
    assert run["status"] == "completed"
    payload = artifacts[0]["payload"]
    assert payload["as_of"] == portfolio().snapshot.as_of.isoformat()
    assert payload["result"]["quotes"]["stock"] is None
    assert "after the analysis date" in str(payload["result"]["issues"])


def test_candidate_ambiguity_returns_typed_result(tmp_path):
    facts = evidence()
    facts.identities["other"] = facts.identities["stock"].model_copy(update={"listing": "XNAS", "currency": "USD"})
    run, artifacts, _ = tool_run(tmp_path, "resolve_candidate", {"ticker": "ACME", "listing": None, "account_id": None},
                                  FakeFinancialProvider(facts))
    assert run["status"] == "completed"
    result = artifacts[0]["payload"]["result"]
    assert result["position"] is None and result["unresolved"]["reason"] == "multiple"
    assert len(result["unresolved"]["listings"]) == 2


def test_candidate_account_ambiguity_never_picks_first_account(tmp_path):
    saved = portfolio()
    saved.snapshot.accounts.append(saved.snapshot.accounts[0].model_copy(update={"id": "rrsp", "name": "RRSP"}))
    run, artifacts, _ = tool_run(tmp_path, "resolve_candidate", {"ticker": "ACME", "listing": "XTSE", "account_id": None},
                                  saved=saved)
    assert run["status"] == "completed"
    clarification = artifacts[0]["payload"]["clarification_required"]
    assert clarification["code"] == "select_account" and clarification["choices"] == ["tfsa", "rrsp"]


def test_quote_refresh_only_updates_cache(tmp_path):
    calls = []
    def yahoo(symbols):
        calls.append(symbols)
        return {"ACME.TO": {"price": "21", "currency": "CAD", "time": int(datetime.now(UTC).timestamp())}}
    cache = QuoteCache(tmp_path / "quotes.json")
    provider = FakeFinancialProvider(cache=cache, yahoo=yahoo)
    run, artifacts, _ = tool_run(tmp_path, "refresh_quotes", {"position_ids": ["stock"], "force": False}, provider)
    assert run["status"] == "completed"
    assert artifacts[0]["payload"]["result"]["fetched"] == ["ACME.TO"]
    assert calls == [["ACME.TO"]] and cache.get()["ACME.TO"].value == 21


@pytest.mark.parametrize("name,args", [
    ("review_portfolio", {"positions": []}),
    ("review_portfolio", {"settings": {"single_company_cap": "1"}}),
    ("get_financial_data", {"position_ids": ["stock"], "as_of": None, "quotes": {}}),
    ("check_proposed_changes", {"trades": [], "new_cash": [{"cash_position_id": "cash", "amount": "100"}]}),
    ("resolve_candidate", {"ticker": "ACME", "listing": None, "account_id": None, "currency": "USD"}),
])
def test_disallowed_replacement_inputs_fail_before_provider_calls(tmp_path, name, args):
    class NeverCalled(FakeFinancialProvider):
        async def identity(self, *args):
            raise AssertionError("Invalid arguments reached financial source")
        async def lookup(self, *args):
            raise AssertionError("Invalid arguments reached identity source")
    run, artifacts, model = tool_run(tmp_path, name, args, NeverCalled())
    assert run["status"] == "failed" and run["error"] == "Invalid application tool arguments."
    assert artifacts == [] and len(model.requests) == 1


def test_candidate_is_composable_retrievable_and_local_to_one_run(tmp_path):
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(portfolio())

    class Compose(CapturingTransport):
        candidate_id = None

        async def respond(self, messages, **kwargs):
            self.requests.append({"messages": messages, **kwargs})
            step = len(self.requests)
            if step == 1:
                name, args = "resolve_candidate", {"ticker": "ACME.TO", "listing": None, "account_id": None}
            elif step == 2:
                envelope = json.loads(messages[-1]["output"])
                assert envelope["reference"]["basis"] == "hypothetical"
                self.candidate_id = envelope["payload"]["result"]["position"]["id"]
                assert envelope["payload"]["result"]["position"]["shares"] == "0"
                name, args = "get_artifact", {"artifact_id": envelope["reference"]["id"]}
            elif step == 3:
                assert json.loads(messages[-1]["output"])["payload"]["result"]["position"]["id"] == self.candidate_id
                name, args = "check_proposed_changes", {"trades": [
                    {"position_id": self.candidate_id, "shares_change": "1", "cash_position_id": "cash"},
                ]}
            elif step == 4:
                assert json.loads(messages[-1]["output"])["payload"]["result"]["post_cash_value"] == "780"
                return ModelTurn(text="This candidate has only been previewed.")
            elif step == 5:
                name, args = "get_financial_data", {"position_ids": [self.candidate_id], "as_of": None}
            else:
                assert json.loads(messages[-1]["output"])["payload"]["clarification_required"]["code"] == "unknown_instrument"
                return ModelTurn(text="Resolve the candidate again for this request.")
            return ModelTurn(calls=[ToolCall(f"step-{step}", name, json.dumps(args))])

    model = Compose([])
    with api(store, model, FakeFinancialProvider(evidence())) as client:
        thread = client.post("/api/threads").json()["id"]
        first = submit(client, thread, "Check buying one Acme share as a candidate.")
        assert first["status"] == "completed"
        assert len(first["artifact_ids"]) >= 2
        assert submit(client, thread, "Get the candidate quote.", "req-2")["status"] == "completed"
        current = client.get("/api/portfolio").json()
        assert current["revision"] == 1 and len(current["portfolio"]["snapshot"]["positions"]) == 2


def test_financial_tool_uses_acceptance_revision_after_later_commit(tmp_path):
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(portfolio())
    class CommitBeforeCalculation(CapturingTransport):
        async def respond(self, messages, **kwargs):
            if not self.requests:
                updated = portfolio()
                updated.snapshot.positions[1].cash = Decimal("9999")
                store.save(SavedPortfolio.model_validate(updated.model_dump()))
            return await super().respond(messages, **kwargs)
    model = CommitBeforeCalculation([ModelTurn(calls=[ToolCall("cash", "review_portfolio", "{}")]), ModelTurn(text="Done.")])
    with api(store, model, FakeFinancialProvider(evidence())) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "How much cash?")
        assert run["status"] == "completed" and run["bound_revision"] == 1
        artifact = client.get(f"/api/artifacts/{run['artifact_ids'][0]}").json()
        assert artifact["reference"]["revision"] == 1
        assert artifact["payload"]["result"]["cash_value"] == "800"
        assert client.get("/api/portfolio").json()["revision"] == 2


@pytest.mark.parametrize("arguments,code", [
    ({"position_ids": ["invented"], "as_of": None}, "unknown_instrument"),
    ({"position_ids": ["stock", "stock"], "as_of": None}, "unknown_instrument"),
    ({"position_ids": ["stock"], "as_of": (datetime.now(MARKET_TIME).date() + timedelta(days=1)).isoformat()}, "unavailable"),
])
def test_invalid_selectors_do_not_reach_financial_source(tmp_path, arguments, code):
    class NeverCalled(FakeFinancialProvider):
        async def identity(self, *args):
            raise AssertionError("Invalid selector reached financial provider")
    run, artifacts, _ = tool_run(tmp_path, "get_financial_data", arguments, NeverCalled())
    assert run["status"] == "completed"
    assert artifacts[0]["payload"]["clarification_required"]["code"] == code


def test_sponsor_holdings_and_fx_remain_typed_dated_evidence(tmp_path):
    saved, facts = portfolio(), evidence()
    today = datetime.now(MARKET_TIME).date().isoformat()
    saved.snapshot.positions[0].kind = "etf"
    saved.snapshot.positions[0].etf_role = "diversified"
    saved.snapshot.reporting_currency = "USD"
    facts.identities["stock"].kind = "etf"
    facts = FinancialEvidence.model_validate({**facts.model_dump(mode="json"),
        "fx": [{"from_currency": "CAD", "to_currency": "USD", "rate": "0.7", "as_of": today, "source": "Fixture"}],
        "sponsor_holdings": {"stock": {"as_of": today, "source": "Sponsor",
            "source_url": "https://example.test/holdings", "coverage": "full", "holdings": []}},
    })
    run, artifacts, _ = tool_run(tmp_path, "get_financial_data", {"position_ids": ["stock"], "as_of": today},
                                  FakeFinancialProvider(facts), saved)
    assert run["status"] == "completed"
    result = artifacts[0]["payload"]["result"]
    assert result["fx"][0]["rate"] == "0.7"
    assert result["sponsor_holdings"]["stock"]["as_of"] == today
    assert result["identities"]["stock"]["kind"] == "etf"


def test_financial_request_timeout_keeps_run_bounded(tmp_path):
    class SlowProvider(FakeFinancialProvider):
        async def identity(self, *args):
            await asyncio.sleep(60)
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(portfolio())
    model = CapturingTransport([ModelTurn(calls=[ToolCall("slow", "review_portfolio", "{}")]), ModelTurn(text="The source timed out.")])
    with TestClient(create_app(model=model, financial=SlowProvider(), portfolio_store=store,
                              settings=Settings(api_key="sk-test", model="test-model", request_timeout=0.05))) as client:
        thread = client.post("/api/threads").json()["id"]
        run = submit(client, thread, "Review exposure.")
        assert run["status"] == "completed" and run["artifact_ids"] == []
        assert "timed out" in json.loads(model.requests[-1]["messages"][-1]["output"])["error"]


@pytest.mark.parametrize("field,value", [("currency", None), ("currency", "USD"), ("status", "unresolved")])
def test_incomplete_or_conflicting_candidate_identity_is_not_guessed(tmp_path, field, value):
    class UnknownCandidate(FakeFinancialProvider):
        async def lookup(self, *args):
            return [evidence().identities["stock"].model_copy(update={field: value})]
    run, artifacts, _ = tool_run(tmp_path, "resolve_candidate",
                                  {"ticker": "ACME", "listing": "XTSE", "account_id": None}, UnknownCandidate())
    assert run["status"] == "completed"
    assert artifacts[0]["payload"]["clarification_required"]["code"] == "unresolved_identity"


def test_candidate_etf_role_stays_unknown_without_sponsor_classification(tmp_path):
    facts = evidence()
    facts.identities["stock"].kind = "etf"
    run, artifacts, _ = tool_run(tmp_path, "resolve_candidate",
                                  {"ticker": "ACME", "listing": "XTSE", "account_id": None}, FakeFinancialProvider(facts))
    assert run["status"] == "completed"
    result = artifacts[0]["payload"]["result"]
    assert result["is_fund"] and result["position"]["etf_role"] is None


def test_direct_historical_evidence_qualifies_unusable_dated_fx(tmp_path):
    facts, saved = evidence(), portfolio()
    saved.snapshot.reporting_currency = "USD"
    facts = FinancialEvidence.model_validate({**facts.model_dump(mode="json"), "fx": [{
        "from_currency": "CAD", "to_currency": "USD", "rate": "0.7",
        "as_of": datetime.now(MARKET_TIME).date().isoformat(), "source": "Fixture", "status": "manual",
    }]})
    run, artifacts, _ = tool_run(tmp_path, "get_financial_data", {
        "position_ids": ["stock"], "as_of": saved.snapshot.as_of.isoformat(),
    }, FakeFinancialProvider(facts), saved)
    assert run["status"] == "completed"
    assert "could not be converted: no usable FX rate" in str(artifacts[0]["payload"]["qualifications"])


@pytest.mark.parametrize("name,args", [("review_portfolio", {}), ("check_proposed_changes", {"trades": [
    {"position_id": "stock", "shares_change": "1", "cash_position_id": "cash"},
]})])
def test_unresolved_saved_holdings_prevent_complete_portfolio_claims(tmp_path, name, args):
    saved = portfolio().model_dump(mode="json")
    saved["unresolved"] = [{"account_id": "tfsa", "ticker": "UNKNOWN", "shares": "10000"}]
    run, artifacts, _ = tool_run(tmp_path, name, args, saved=SavedPortfolio.model_validate(saved))
    assert run["status"] == "completed"
    assert artifacts[0]["payload"]["clarification_required"]["code"] == "unresolved_identity"


def test_nyse_arca_etf_is_a_priced_us_listing():
    """Most US ETFs (VTI, VOO, SPY) list on NYSE Arca; a holding identified there is priced like any US listing."""
    from analyst.financial_data import eodhd_symbol, yahoo_symbol
    from analyst.schemas import Position, is_canadian_security
    vti = Position(id="vti", account_id="taxable", kind="etf", currency="USD", ticker="VTI", listing="ARCX", shares="5")
    assert eodhd_symbol(vti) == "VTI.US" and yahoo_symbol(vti) == "VTI"
    assert not is_canadian_security("USD", "ARCX")


def test_followup_reads_prior_result_without_tool_records_or_duplicate_links(tmp_path: Path) -> None:
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(portfolio())

    class FollowUp(CapturingTransport):
        async def respond(self, messages, **kwargs):
            if len(self.requests) == 2:
                context = json.loads(messages[1]["content"])
                prior = next(r["id"] for r in context["artifact_references"] if r["kind"] == "calculation_result")
                self.turns = iter([ModelTurn(calls=[ToolCall("again", "get_artifact", json.dumps({"artifact_id": prior}))]),
                                   ModelTurn(text="Cash is still CAD 800.")])
            return await super().respond(messages, **kwargs)

    model = FollowUp([ModelTurn(calls=[ToolCall("exposure", "review_portfolio", "{}")]),
                      ModelTurn(text="Cash is CAD 800.")])
    with api(store, model, FakeFinancialProvider(evidence())) as client:
        thread = client.post("/api/threads").json()["id"]
        first = submit(client, thread, "How much cash do I have?")
        second = submit(client, thread, "Remind me of the cash figure.", "req-2")
        messages = client.get(f"/api/threads/{thread}").json()["messages"]
    assert first["status"] == second["status"] == "completed"
    assert len(first["artifact_ids"]) == 1
    # Reading the earlier result links nothing new: no second card, no run artifact.
    assert second["artifact_ids"] == []
    read_step = next(m for m in messages if m["tool_outcomes"] and m["tool_outcomes"][0]["name"] == "get_artifact")
    assert read_step["artifact_ids"] == [] and read_step["tool_outcomes"][0]["artifact_ids"] == []
    assert read_step["tool_outcomes"][0]["status"] == "success"
    # History holds the real dialogue only; the answer carries its run's tool outcomes and artifact.
    history = model.requests[2]["messages"][2:]
    assert [m["role"] for m in history] == ["user", "assistant", "user"]
    assert "retrieval" not in json.dumps(history)
    answer = json.loads(history[1]["content"])
    assert answer["text"] == "Cash is CAD 800."
    assert answer["completed_tools"][0]["artifact_ids"] == first["artifact_ids"]
    output = json.loads(model.requests[3]["messages"][-1]["output"])
    assert output["reference"]["id"] == first["artifact_ids"][0] and output["payload"]["result"]["cash_value"] == "800"
