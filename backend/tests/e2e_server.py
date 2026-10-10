"""Only imported by Playwright's local test server; production has no fake-mode flag."""

import asyncio
import json
import socket
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from analyst import pipeline
from analyst.api import create_app
from analyst.config import Settings
from analyst.decisions import DecisionStore
from analyst.portfolio import PortfolioStore
from analyst.financial_data import FakeFinancialProvider
from analyst.providers import FakeDataProvider, ModelTurn, ToolCall
from analyst.research import ReviewedResearchProvider
from analyst.schemas import (
    NEW_CASH_DESTINATION,
    AnalysisRequest,
    CompanyResearch,
    FinancialEvidence,
    Identity,
    Quote,
    Snapshot,
    SponsorHoldings,
    SourceQualification,
)
from tests.test_allocation import allocation_answer, allocation_evidence, comparison_judgments
from tests.test_canadian_stock import canadian_research_fixture
from tests.test_comparison import case_drivers, driver, judgments
from tests.test_freshness import evidence_fixture
from tests.test_portfolio_review import assessment, review_answer, review_research_fixture
from tests.test_stock import (
    company_judgments,
    research_fixture,
    stock_answer,
    stock_comparison_judgments,
)

# No network outside the local test servers, even if a provider is changed accidentally.
original_connect = socket.socket.connect


def local_connect(sock: socket.socket, address: Any) -> None:
    if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
        raise AssertionError("E2E tests must not contact live services.")
    original_connect(sock, address)


socket.socket.connect = local_connect  # type: ignore[method-assign]


class BrowserTestModel:
    async def respond(self, messages: list[dict[str, Any]], *, require_tool: bool, forced_tool: str | None = None, reasoning_effort: str | None = None) -> ModelTurn:
        if require_tool:
            if json.loads(messages[1]["content"])["question"] == "Slow model review":
                await asyncio.sleep(31)
            return ModelTurn(calls=[ToolCall("browser_tool", "review_portfolio", "{}")])
        request = json.loads(messages[1]["content"])
        tool = json.loads(messages[-1]["output"])
        if request.get("theme"):
            from tests.test_theme import theme_turns
            scripted = theme_turns(request)
            called = {item["name"] for item in messages if item.get("type") == "function_call"}
            for turn in scripted[:-1]:
                if turn.calls[0].name not in called:
                    return turn
            return scripted[-1]
        if request.get("new_cash"):
            called = {item["name"] for item in messages if item.get("type") == "function_call"}
            rows = request["portfolio"]["positions"]
            fund = next(row["id"] for row in rows if row.get("ticker") == "BROAD")
            # Without an existing cash balance the fund is a larger share of the funded portfolio.
            held_cash = any(row["kind"] == "cash" and row["id"] != NEW_CASH_DESTINATION for row in rows)
            low, high = ("0.55", "0.65") if held_cash else ("0.6", "0.7")
            for name, args in [("scan_opportunities", {}), ("calculate_comparison", comparison_judgments((fund, "cash", "keep"))),
                               ("size_allocation", {"position_id": fund, "min_weight": low, "max_weight": high,
                                                    "reason": "Diversification and a retained reserve justify this exposure range."})]:
                if name not in called:
                    return ModelTurn(calls=[ToolCall(name, name, json.dumps(args))])
            return ModelTurn(answer=allocation_answer())
        if request.get("portfolio_review"):
            called = {item["name"] for item in messages if item.get("type") == "function_call"}
            # Desk rebalance journeys: a concentration question gets a "keep everything" answer; the rest reduce p1.
            keep = "concentrated" in request["question"]
            # The pipeline forces each holding and then the comparison; older calls are compacted out of messages.
            if forced_tool == "reunderwrite_holding":
                args = assessment()
                if not request["portfolio_review"]["prior_theses"]:
                    args["assessment"]["status"] = "unknown"
                if keep:
                    args["assessment"].update(action="hold", current_thesis="Demand evidence is mixed but does not break the thesis.")
                return ModelTurn(calls=[ToolCall("thesis", "reunderwrite_holding", json.dumps(args))])
            if forced_tool == "calculate_comparison":
                return ModelTurn(calls=[ToolCall("comparison", "calculate_comparison", json.dumps(stock_comparison_judgments(request)))])
            if keep:
                return ModelTurn(answer={**review_answer(), "preferred_action": "no_action", "evidence_ids": [],
                    "reason": "No holding has evidence strong enough to justify a trade; changing nothing is the better choice.",
                    "alternatives": [{"action": "clarify_inputs", "reason": "Supplying loss tolerance and prior theses could sharpen the view."}]})
            if request["question"] in {"Review supported reduction", "What should I reduce?"} and "size_review" not in called:
                rows = request["portfolio"]["positions"]
                p1 = next(row for row in rows if row["id"] == "p1")
                cash = next((row["id"] for row in rows if row["kind"] == "cash" and row["account_id"] == p1["account_id"] and row["currency"] == p1["currency"]), "c2")
                return ModelTurn(calls=[ToolCall("sizing", "size_review", json.dumps({"position_id": "p1", "cash_position_id": cash, "min_weight": "0.4", "max_weight": "0.5", "reason": "Weaker demand justifies lower issuer exposure and a retained reserve."}))])
            answer = review_answer()
            answer.update(preferred_action="reduce", reason="Weaker demand warrants a conditional reduction despite prior ownership.",
                          alternatives=[{"action": "no_action", "reason": "Retaining exposure is conditional on the current demand evidence improving."}],
                          evidence_ids=["review-p1-filing", "review-p1-issuer"])
            return ModelTurn(answer=answer)
        if request.get("stock"):
            target_id = request["stock"].get("position_id")
            target_pos = next((p for p in request.get("portfolio", {}).get("positions", []) if p.get("id") == target_id), None)
            is_canadian = target_pos and (target_pos.get("currency") == "CAD" or target_pos.get("listing") in {"XTSE", "XTSX", "NEOE", "XCNQ"})
            filing_tool = "get_sedar_filings" if is_canadian else "get_sec_filings"
            called = {item["name"] for item in messages if item.get("type") == "function_call"}
            bound_request = request if request.get("comparison") else AnalysisRequest.model_validate(request).model_dump(mode="json")
            for name, args in [(filing_tool, {}), ("get_issuer_material", {}),
                               ("calculate_company_cases", company_judgments()),
                               ("calculate_comparison", stock_comparison_judgments(bound_request))]:
                if name not in called:
                    return ModelTurn(calls=[ToolCall(name, name, json.dumps(args))])
            answer = stock_answer()
            if is_canadian:
                answer["evidence_ids"] = ["sedar_filing", "issuer_release"]
            if "Review unavailable primary" in request["question"]:
                answer["evidence_ids"] = []
            return ModelTurn(answer=answer)
        if request.get("comparison") and "total_value" in tool:
            template = judgments()["alternatives"][0]["cases"]
            positions = {row["id"]: row for row in request["portfolio"]["positions"]}
            selected = []
            for alt in request["comparison"]["alternatives"]:
                ids = request["comparison"]["scope_position_ids"] if alt["kind"] == "no_action" else [alt["position_id"]]
                drivers = [driver(key, cash=positions[key]["kind"] == "cash") for key in ids]
                selected.append({"alternative_id": alt["id"], "cases": [
                    {**case, "drivers": case_drivers(drivers, case["name"])} for case in template
                ]})
            return ModelTurn(calls=[ToolCall("browser_comparison", "calculate_comparison", json.dumps({"alternatives": selected}))])
        if request["question"] == "Check model proposal" and "total_value" in tool:
            return ModelTurn(calls=[ToolCall("browser_proposal", "check_proposed_changes", json.dumps({
                "new_cash": [], "trades": [{"position_id": "p2", "shares_change": "2", "cash_position_id": "c2"}],
            }))])
        # Actual deterministic tool output has to exist; this provider cannot skip it.
        assert "total_value" in tool and "direct_companies" in tool or "post_total_value" in tool and "guardrails" in tool or "alternatives" in tool and "calculation_basis" in tool
        answer = {
            "preferred_action": "review_only",
            "amount": None,
            "reason": f"Regarding '{request['question']}': review the supplied direct company exposure across accounts.",
            "alternatives": [
                {
                    "action": "clarify_inputs",
                    "reason": "Supply a baseline and personal risk context.",
                }
            ],
            "downside": "Company-specific losses can affect concentrated holdings.",
            "assumptions": ["Supplied marks represent this dated snapshot."],
            "uncertainty": ["Personal guardrails and indirect fund overlap remain unknown."],
            "what_could_change": [
                "Verified identities, updated marks or risk context could change the review."
            ],
        }
        if request["question"] == "Return invalid output":
            answer["amount"] = "1000"
        if request["question"] == "Check model proposal":
            answer.update(preferred_action="no_action", reason="Strong conviction permits an exception to configured limits.")
            answer["downside"] = "Strong conviction permits an exception in the downside explanation."
        if request.get("comparison"):
            answer.update(
                preferred_action="no_action",
                reason="The compared cases do not establish a superior action while fund exposure evidence and personal risk context remain provisional. Retaining the actual holdings and cash is a conditional alternative.",
                alternatives=[{"action": "clarify_inputs", "reason": "Confirm fund exposure, costs, income and personal risk context before deciding."}],
                downside="The diversified fund retains equity and currency downside; cash faces falling reinvestment rates. No action retains the existing mix and its risks.",
                assumptions=["Exposure, income, rate and currency paths are judgments; calculations use the dated common capital basis."],
                uncertainty=["Unknown transaction costs and personal tax consequences leave terminal values incomplete."],
                what_could_change=["Supported fund facts, revised rate or currency paths, and supplied risk context could distinguish the alternatives."],
            )
        return ModelTurn(answer=answer)


def e2e_evidence() -> FinancialEvidence:
    now = datetime.now(timezone.utc)
    evidence = FinancialEvidence()
    # NVDA candidate (stock)
    evidence.identities["nvda"] = Identity(
        status="verified",
        ticker="NVDA",
        listing="XNAS",
        currency="USD",
        kind="stock",
        company_id="nvda",
        company_name="NVIDIA Corporation",
        source="E2E test reference",
        source_url="https://sec.gov/edgar",
        as_of=date(2026, 9, 30),
        captured_at=now,
    )
    evidence.quotes["NVDA"] = Quote(
        value=Decimal("120"),
        as_of=date(2026, 9, 30),
        source="Test provider",
        captured_at=now,
        basis="unadjusted",
        ticker="NVDA",
        listing="XNAS",
        currency="USD",
        status="indicative",
        qualification=SourceQualification(
            source="Test provider",
            terms_url="https://example.com",
            checked_on=date(2026, 9, 30),
            personal_use_permitted=True,
            covered_listings=["XNAS"],
        ),
    )
    # SPY candidate (fund)
    evidence.identities["spy"] = Identity(
        status="verified",
        ticker="SPY",
        listing="XNYS",
        currency="USD",
        kind="etf",
        company_id="spy",
        company_name="SPDR S&P 500 ETF Trust",
        source="E2E test reference",
        source_url="https://sec.gov/edgar",
        as_of=date(2026, 9, 30),
        captured_at=now,
    )
    evidence.quotes["SPY"] = Quote(
        value=Decimal("500"),
        as_of=date(2026, 9, 30),
        source="Test provider",
        captured_at=now,
        basis="unadjusted",
        ticker="SPY",
        listing="XNYS",
        currency="USD",
        status="indicative",
        qualification=SourceQualification(
            source="Test provider",
            terms_url="https://example.com",
            checked_on=date(2026, 9, 30),
            personal_use_permitted=True,
            covered_listings=["XNYS"],
        ),
    )
    # BMO dual-listed
    evidence.identities["bmo_us"] = Identity(
        status="verified",
        ticker="BMO",
        listing="XNYS",
        currency="USD",
        kind="stock",
        company_id="bmo",
        company_name="Bank of Montreal",
        source="E2E test reference",
        source_url="https://sec.gov/edgar",
        as_of=date(2026, 9, 30),
        captured_at=now,
    )
    evidence.identities["bmo_ca"] = Identity(
        status="verified",
        ticker="BMO",
        listing="XTSE",
        currency="CAD",
        kind="stock",
        company_id="bmo",
        company_name="Bank of Montreal",
        source="E2E test reference",
        source_url="https://sedarplus.ca",
        as_of=date(2026, 9, 30),
        captured_at=now,
    )
    evidence.quotes["BMO"] = Quote(
        value=Decimal("130"),
        as_of=date(2026, 9, 30),
        source="Test provider",
        captured_at=now,
        basis="unadjusted",
        ticker="BMO",
        listing="XTSE",
        currency="CAD",
        status="indicative",
        qualification=SourceQualification(
            source="Test provider",
            terms_url="https://example.com",
            checked_on=date(2026, 9, 30),
            personal_use_permitted=True,
            covered_listings=["XTSE", "XNYS"],
        ),
    )
    return evidence


def e2e_research() -> dict[str, CompanyResearch]:
    records = {
        "acme": CompanyResearch.model_validate(review_research_fixture()),
        "acme:XTSE": CompanyResearch.model_validate(canadian_research_fixture()),
    }
    nvda_res = research_fixture()
    nvda_res["company_id"] = "nvda"
    for doc in nvda_res["documents"]:
        doc["company_id"] = "nvda"
    records["nvda"] = CompanyResearch.model_validate(nvda_res)

    bmo_res = canadian_research_fixture()
    bmo_res["company_id"] = "bmo"
    for doc in bmo_res["documents"]:
        doc["company_id"] = "bmo"
    records["bmo"] = CompanyResearch.model_validate(bmo_res)
    records["bmo:XTSE"] = CompanyResearch.model_validate(bmo_res)
    return records


financial = FakeFinancialProvider()
financial.reference = e2e_evidence()
stock_research = ReviewedResearchProvider(e2e_research())


class BrowserTestData(FakeDataProvider):
    def snapshot(self, supplied: Snapshot) -> Snapshot:
        # Browser journeys run serially. Bind the external source fixture to the
        # submitted listing; every request resets it, including ordinary broker marks.
        financial.reference = e2e_evidence()
        stock_research.records = e2e_research()
        for position in supplied.positions:
            if position.id == "fund" and position.mark and position.mark.source == "Fixture allocation":
                financial.reference = allocation_evidence()
            if position.ticker == "BROAD" and position.kind == "etf" and position.mark is None:
                # A simple holdings import: the source fixture follows the application-made position ID.
                financial.reference = FinancialEvidence.model_validate_json(allocation_evidence().model_dump_json().replace('"fund"', json.dumps(position.id)))
            if position.id == "p2" and position.mark and position.mark.source.startswith("Fixture "):
                scenario = position.mark.source.removeprefix("Fixture ")
                if scenario == "missing_research":
                    stock_research.records = {}
                    continue
            if position.id == "p1" and position.mark and position.mark.source.startswith("Fixture "):
                fixture = evidence_fixture()
                scenario = position.mark.source.removeprefix("Fixture ")
                if scenario == "missing_research":
                    stock_research.records = {}
                    continue
                if scenario == "ambiguous":
                    fixture["identities"]["p1"]["status"] = "ambiguous"
                else:
                    fixture["quotes"]["p1"]["status"] = scenario
                financial.reference = FinancialEvidence.model_validate(fixture)
            if position.kind == "etf" and position.mark and position.mark.source.startswith("Fixture sponsor "):
                scenario = position.mark.source.removeprefix("Fixture sponsor ")
                as_of = "2025-12-31" if scenario == "stale" else supplied.as_of
                coverage = "stale" if scenario == "stale" else "partial" if scenario == "partial" else "full"
                holdings_obj = SponsorHoldings.model_validate({
                    "as_of": as_of,
                    "source": "Top 10 holdings" if scenario == "partial" else "Full holdings file",
                    "coverage": coverage,
                    "holdings": [
                        {
                            "company_id": "acme",
                            "company_name": "Acme Corp",
                            "ticker": "ACME",
                            "listing": "XNAS",
                            "kind": "stock",
                            "weight": "0.10",
                        }
                    ],
                })
                financial.reference.sponsor_holdings[position.id] = holdings_obj
        return super().snapshot(supplied)


e2e_storage_dir = Path(__file__).parent / "data" / "e2e_decisions"
decision_store = DecisionStore(e2e_storage_dir)
for _file in decision_store.directory.glob("*.json"):
    try:
        _file.unlink()
    except OSError:
        pass

# Browser fixtures are dated snapshots: value them on their own date, as the backend tests do.
pipeline.market_today = lambda: date(2000, 1, 1)
portfolio_store = PortfolioStore(Path(__file__).parent / "data" / "e2e_portfolio")
portfolio_store.path.unlink(missing_ok=True)

app = create_app(
    model=BrowserTestModel(),
    data=BrowserTestData(),
    financial=financial,
    research=stock_research,
    settings=Settings("sk-test-backend-only-never-browser", "test-model"),
    store=decision_store,
    portfolio_store=portfolio_store,
)


@app.delete("/api/test/portfolio")
def forget_portfolio() -> None:
    """Test server only: each browser journey starts without a saved portfolio or a leftover source fixture."""
    portfolio_store.path.unlink(missing_ok=True)
    financial.reference = e2e_evidence()
    stock_research.records = e2e_research()

