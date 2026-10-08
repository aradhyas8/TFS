"""SEC research for foreign private issuers (20-F / 6-K), against fixed EDGAR fixtures. No test touches the network."""

import asyncio
import copy
import json
from datetime import date
from decimal import Decimal

import httpx
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.providers import FakeDataProvider, ModelTurn, ScriptedModel, ToolCall
from analyst.schemas import AnalysisRequest, CompanyResearch, Position
from analyst.sec_research import SecCache, SecResearchProvider
from tests.test_analysis import snapshot
from tests.test_stock import company_judgments, stock_answer, stock_comparison_judgments

CIK = 654321
COMPANY = f"CIK{CIK:010d}"
AGENT = "Test test@example.test"
TWENTY_F, OLD_20F, INTERIM, RESULTS, NOTICE, AGM, DEAL = (
    "0000654321-26-000020", "0000654321-25-000020", "0000654321-26-000060", "0000654321-26-000050",
    "0000654321-26-000055", "0000654321-26-000057", "0000654321-26-000058")
FACTS_URL = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK:010d}.json"
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{CIK:010d}.json"
FILED = {TWENTY_F: "2026-06-20", OLD_20F: "2025-06-20", INTERIM: "2026-11-10"}
FY = [("2025-04-01", "2026-03-31"), ("2024-04-01", "2025-03-31")]
QUARTER, HALF = ("2026-07-01", "2026-09-30"), ("2026-04-01", "2026-09-30")
AS_OF = date(2026, 11, 30)


def row(value, end, accn, start=None, fp="FY"):
    form = "6-K" if accn == INTERIM else "20-F"
    return {**({"start": start} if start else {}), "end": end, "val": value, "accn": accn, "fy": 2026, "fp": fp, "form": form, "filed": FILED[accn]}


def annual(*values, accn=TWENTY_F):
    return [row(value, end, accn, start) for value, (start, end) in zip(values, FY, strict=False)]


def ifrs(currency="INR"):
    """An IFRS filer reporting in INR, with a USD convenience translation of the latest year beside it."""
    money = lambda rows, usd=None: {"units": {currency: rows, **({"USD": usd} if usd and currency != "USD" else {})}}  # noqa: E731
    return {
        "RevenueFromContractsWithCustomers": money([*annual(800_000, 700_000),
                                                    row(220_000, QUARTER[1], INTERIM, QUARTER[0], "Q2"), row(430_000, HALF[1], INTERIM, HALF[0], "Q2")],
                                                   usd=[row(9_600, FY[0][1], TWENTY_F, FY[0][0])]),
        "ProfitLossFromOperatingActivities": money(annual(160_000, 140_000)),
        "ProfitLossAttributableToOwnersOfParent": money([*annual(120_000, 100_000), row(33_000, QUARTER[1], INTERIM, QUARTER[0], "Q2")]),
        "ProfitLoss": money(annual(125_000, 104_000)),  # includes minority interests: not an alias for net income
        "CashFlowsFromUsedInOperatingActivities": money([*annual(200_000, 170_000), row(95_000, HALF[1], INTERIM, HALF[0], "Q2")]),
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": money([*annual(50_000, 45_000), row(20_000, HALF[1], INTERIM, HALF[0], "Q2")]),
        "AdjustedWeightedAverageShares": {"units": {"shares": [*annual(4_000, 3_990), row(4_010, QUARTER[1], INTERIM, QUARTER[0], "Q2")]}},
        "WeightedAverageShares": {"units": {"shares": annual(3_980, 3_970)}},
        "CashAndCashEquivalents": money([row(90_000, FY[0][1], TWENTY_F), row(97_000, HALF[1], INTERIM, fp="Q2")]),
        "Borrowings": money([row(40_000, FY[0][1], TWENTY_F)]),
    }


def companyfacts(taxonomy=None):
    return {"cik": CIK, "entityName": "Foreign Example Ltd", "facts": {"ifrs-full": taxonomy or ifrs()}}


def submissions():
    filings = [("6-K", "2026-11-10", "", INTERIM, "frn-20260930.htm", "6-K"),
               ("6-K", "2026-09-25", "", DEAL, "frn6k-deal.htm", "FORM 6-K"),
               ("6-K", "2026-09-20", "", RESULTS, "frn6k-results.htm", "FORM 6-K"),
               ("6-K", "2026-09-15", "", AGM, "frn6k-agm.htm", "FORM 6-K"),
               ("6-K", "2026-09-10", "", NOTICE, "frn6k-notice.htm", "FORM 6-K"),
               ("20-F", "2026-06-20", "2026-03-31", TWENTY_F, "frn-20260331.htm", "FORM 20-F"),
               ("20-F", "2025-06-20", "2025-03-31", OLD_20F, "frn-20250331.htm", "FORM 20-F")]
    keys = ("form", "filingDate", "reportDate", "accessionNumber", "primaryDocument", "primaryDocDescription")
    return {"cik": str(CIK), "name": "Foreign Example Ltd", "sic": "7371",
            "filings": {"recent": {key: [filing[index] for filing in filings] for index, key in enumerate(keys)}}}


def folder(accession):
    return f"https://www.sec.gov/Archives/edgar/data/{CIK}/{accession.replace('-', '')}"


PAGES = {  # what each 6-K says; only the results release and the acquisition are relevant
    RESULTS: ("frn-ex99-1.htm", "<p>Sub: Unaudited consolidated financial results for the quarter ended June 30, 2026.</p>"
                                "<p>Revenue was &#8377;220,000 crore.</p>"),
    NOTICE: ("frn-ex99.htm", "<p>Sub: Board meeting. A meeting of the Board is scheduled to be held on October 17, 2026 to consider "
                             "and approve the unaudited financial results for the quarter.</p>"),
    AGM: ("frn6k-agm.htm", "<p>Sub: Postal ballot notice and annual general meeting arrangements.</p>"),
    DEAL: ("frn-ex99.htm", "<p>Sub: Disclosure. The Company has completed the acquisition of Example Analytics GmbH.</p>"),
    INTERIM: ("frn-20260930.htm", "<p>Interim condensed financial statements.</p>"),
}


class Edgar:
    def __init__(self, facts=None, filings=None) -> None:
        self.facts, self.filings = facts or companyfacts(), filings or submissions()
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url == FACTS_URL:
            return httpx.Response(200, json=self.facts) if self.facts != "missing" else httpx.Response(404)
        if url == SUBMISSIONS_URL:
            return httpx.Response(200, json=self.filings)
        for accession, (name, page) in PAGES.items():
            if url == f"{folder(accession)}/index.json":
                return httpx.Response(200, json={"directory": {"item": [{"name": "cover.htm"}, {"name": name}]}})
            if url == f"{folder(accession)}/{name}":
                return httpx.Response(200, text=f"<html><body>{page}</body></html>")
        return httpx.Response(404)


POSITION = Position(id="p1", account_id="tfsa", kind="stock", currency="USD", ticker="FRN", listing="XNYS",
                    company_id=COMPANY, company_name="Foreign Example Ltd", shares=Decimal(10))


def provider(tmp_path, edgar):
    return SecResearchProvider(agent=AGENT, cache=SecCache(tmp_path / "sec"), transport=httpx.MockTransport(edgar))


def research(tmp_path, edgar=None, as_of=AS_OF) -> CompanyResearch:
    return asyncio.run(provider(tmp_path, edgar or Edgar()).company(POSITION, as_of))


def by_id(result):
    return {fact.id: fact for fact in result.facts}


def test_normal_20f_company_with_ifrs_aliases_in_its_reporting_currency(tmp_path):
    result = research(tmp_path)
    facts = by_id(result)
    assert result.sector == "other"
    assert facts["revenue-fy-2026-03-31"].value == 800_000 and facts["revenue-fy-2025-03-31"].value == 700_000
    assert facts["operating_income-fy-2026-03-31"].value == 160_000
    assert facts["net_income-fy-2026-03-31"].value == 120_000  # owners of the parent, not ProfitLoss incl. minorities
    assert facts["shares-fy-2026-03-31"].value == 4_000  # diluted (AdjustedWeightedAverageShares)
    assert facts["cash-at-2026-03-31"].value == 90_000 and facts["total_debt-at-2026-03-31"].value == 40_000
    # Every money figure stays in INR; the USD convenience translation is never used.
    assert {fact.currency for fact in result.facts if fact.unit == "currency"} == {"INR"}
    assert all(source.unit in {"INR", "shares"} and source.taxonomy == "ifrs-full" for fact in result.facts for source in fact.sources)
    revenue = facts["revenue-fy-2026-03-31"].sources[0]
    assert (revenue.concept, revenue.accession, revenue.form, revenue.fiscal_period, revenue.filed, revenue.value, revenue.url) == (
        "RevenueFromContractsWithCustomers", TWENTY_F, "20-F", "FY", date(2026, 6, 20), 800_000, FACTS_URL)
    assert all(fact.review == "sec_xbrl" and fact.custom_tags_checked for fact in result.facts)
    annual_doc = next(doc for doc in result.documents if doc.id == f"sec-20-f-{TWENTY_F}")
    assert annual_doc.title == "Form 20-F, fiscal year ended 2026-03-31" and "800,000 INR" in annual_doc.excerpt


def test_free_cash_flow_is_calculated_from_reported_parts(tmp_path):
    facts = by_id(research(tmp_path))
    fcf = facts["free_cash_flow-fy-2026-03-31"]
    assert fcf.value == 150_000 and fcf.currency == "INR"  # 200,000 - 50,000
    assert [source.concept for source in fcf.sources] == ["CashFlowsFromUsedInOperatingActivities",
                                                          "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"]
    assert facts["free_cash_flow-ytd-2026-09-30"].value == 75_000  # 95,000 - 20,000, half year to date


def test_xbrl_6k_interim_period_and_year_to_date_cash_flow(tmp_path):
    result = research(tmp_path)
    facts = by_id(result)
    quarter = facts["revenue-q-2026-09-30"]
    assert (quarter.value, quarter.period_start, quarter.document_ids) == (220_000, date(2026, 7, 1), [f"sec-6-k-{INTERIM}"])
    assert facts["net_income-q-2026-09-30"].value == 33_000 and facts["cash-at-2026-09-30"].value == 97_000
    # The 6-K reports cash flow for the half year only: it is kept as year to date, and no quarter is derived from it.
    assert "operating_cash_flow-ytd-2026-09-30" in facts and "operating_cash_flow-q-2026-09-30" not in facts
    assert "free_cash_flow-q-2026-09-30" not in facts
    interim = next(doc for doc in result.documents if doc.id == f"sec-6-k-{INTERIM}")
    assert interim.title == "Form 6-K, interim period ended 2026-09-30"
    # Before the interim 6-K was filed, the 20-F is the latest period.
    early = by_id(research(tmp_path / "early", as_of=date(2026, 10, 30)))
    assert "revenue-q-2026-09-30" not in early and "revenue-fy-2026-03-31" in early


def test_relevant_6ks_only(tmp_path):
    docs = {doc.id: doc for doc in research(tmp_path).documents}
    release = docs[f"issuer-6k-{RESULTS}"]
    assert (release.authority, release.url) == ("issuer", f"{folder(RESULTS)}/frn-ex99-1.htm")
    assert release.excerpt.startswith("Sub: Unaudited consolidated financial results") and "₹220,000 crore" in release.excerpt
    assert docs[f"sec-6k-{DEAL}"].title == "Form 6-K (2026-09-25): material update"
    # The board-meeting notice and the AGM notice are read once but not used as evidence.
    assert not any(NOTICE in key or AGM in key for key in docs)


def test_conflicting_alternative_tags_stay_unknown(tmp_path):
    taxonomy = ifrs()
    taxonomy["Revenue"] = {"units": {"INR": annual(810_000, 700_000)}}  # disagrees for the latest year only
    result = research(tmp_path, Edgar(facts=companyfacts(taxonomy)))
    facts = by_id(result)
    assert facts["revenue-fy-2026-03-31"].custom_tags_checked is False  # unusable to the calculator
    assert facts["revenue-fy-2025-03-31"].custom_tags_checked is True
    assert any("revenue for the period ending 2026-03-31: alternative or duplicate XBRL tags disagree (Revenue, RevenueFromContractsWithCustomers)"
               in issue for issue in result.issues)


def test_missing_metric_stays_unknown(tmp_path):
    taxonomy = ifrs()
    del taxonomy["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"], taxonomy["Borrowings"]
    result = research(tmp_path, Edgar(facts=companyfacts(taxonomy)))
    facts = by_id(result)
    assert not [fact for fact in result.facts if fact.metric in {"capex", "free_cash_flow", "total_debt"}]
    assert facts["operating_cash_flow-fy-2026-03-31"].value == 200_000
    assert any(issue.startswith("capex for the period ending 2026-03-31: not reported under") for issue in result.issues)
    assert any(issue.startswith("total_debt at 2026-03-31: no complete debt figures") for issue in result.issues)


def test_basic_shares_only_when_no_diluted_count(tmp_path):
    taxonomy = ifrs()
    del taxonomy["AdjustedWeightedAverageShares"]
    shares = by_id(research(tmp_path, Edgar(facts=companyfacts(taxonomy))))["shares-fy-2026-03-31"]
    assert shares.value == 3_980 and shares.definition.startswith("Weighted-average basic shares outstanding (no diluted count reported)")


def test_latest_20f_not_yet_in_company_facts_uses_the_latest_one_that_is(tmp_path):
    # SEC's company facts still end at the previous 20-F: its fiscal years are used, the newer 20-F is listed without figures.
    taxonomy = json.loads(json.dumps(ifrs()).replace(TWENTY_F, OLD_20F))
    result = research(tmp_path, Edgar(facts=companyfacts(taxonomy)))
    assert f"sec-20-f-{TWENTY_F}" in {doc.id for doc in result.documents}
    facts = by_id(result)
    assert facts["revenue-fy-2025-03-31"].sources[0].accession == OLD_20F and facts["revenue-fy-2025-03-31"].value == 700_000
    assert "revenue-fy-2026-03-31" not in facts
    assert any(f"20-F {TWENTY_F} (filed 2026-06-20) has no figures in SEC company facts yet" in issue for issue in result.issues)


def test_filer_without_xbrl_keeps_filings_as_evidence(tmp_path):
    result = research(tmp_path, Edgar(facts="missing"))
    assert result.facts == []
    assert {f"sec-20-f-{TWENTY_F}", f"issuer-6k-{RESULTS}"} <= {doc.id for doc in result.documents}
    assert "SEC publishes no XBRL company facts for this filer." in result.issues


def test_two_reporting_currencies_are_ambiguous(tmp_path):
    taxonomy = ifrs()
    taxonomy["ProfitLossFromOperatingActivities"]["units"]["EUR"] = annual(1)
    result = research(tmp_path, Edgar(facts=companyfacts(taxonomy)))
    assert not [fact for fact in result.facts if fact.unit == "currency" and fact.period_end == date(2026, 3, 31)]
    assert any("reporting currency is ambiguous" in issue for issue in result.issues)


def stock_request():
    portfolio = json.loads(json.dumps(snapshot()).replace('"acme"', json.dumps(COMPANY)))
    return {"question": "What do you think about FRN?", "portfolio": portfolio, "stock": {"position_id": "p1"}}


def analyze(source):
    judgments = company_judgments()
    judgments.update(revenue_fact_id="revenue-fy-2026-03-31", shares_fact_id="shares-fy-2026-03-31")
    answer = copy.deepcopy(stock_answer())
    answer["evidence_ids"] = [f"sec-20-f-{TWENTY_F}", f"issuer-6k-{RESULTS}"]
    bound = AnalysisRequest.model_validate(stock_request()).model_dump(mode="json")
    model = ScriptedModel([ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
                           ModelTurn(calls=[ToolCall("sec", "get_sec_filings", "{}")]),
                           ModelTurn(calls=[ToolCall("issuer", "get_issuer_material", "{}")]),
                           ModelTurn(calls=[ToolCall("company", "calculate_company_cases", json.dumps(judgments))]),
                           ModelTurn(calls=[ToolCall("comparison", "calculate_comparison", json.dumps(stock_comparison_judgments(bound)))]),
                           ModelTurn(answer=answer)])
    response = TestClient(create_app(model=model, data=FakeDataProvider(), research=source)).post("/api/analyze", json=stock_request())
    assert response.status_code == 200, response.text
    return response.json()


def test_usd_reporting_20f_filer_runs_stock_analysis_and_repeats_with_zero_sec_requests(tmp_path):
    edgar = Edgar(facts=companyfacts(ifrs("USD")))
    first = analyze(provider(tmp_path, edgar))
    # 800,000 revenue x 10% margin x 10 exit multiple / 4,000 diluted shares = 200 per share in the base case.
    assert [case["terminal_price"] for case in first["stock"]["cases"]] == ["100", "200", "300"]
    assert first["recommendation"]["preferred_action"] == "hold"
    assert edgar.requests
    edgar.requests = []
    again = analyze(provider(tmp_path, edgar))
    assert edgar.requests == [] and again["stock"]["cases"] == first["stock"]["cases"]


def test_inr_figures_are_not_valued_against_a_usd_price(tmp_path):
    stock = analyze(provider(tmp_path, Edgar()))["stock"]
    # The calculator only compares figures in the position's currency; INR statements are not silently converted.
    assert all(case["terminal_price"] is None for case in stock["cases"])


def analyze_with(source, company_calls):
    judgments = company_judgments()
    judgments.update(revenue_fact_id="revenue-fy-2026-03-31", shares_fact_id="shares-fy-2026-03-31")
    answer = copy.deepcopy(stock_answer())
    answer["evidence_ids"] = [f"sec-20-f-{TWENTY_F}", f"issuer-6k-{RESULTS}"]
    bound = AnalysisRequest.model_validate(stock_request()).model_dump(mode="json")
    model = ScriptedModel([ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
                           ModelTurn(calls=[ToolCall("sec", "get_sec_filings", "{}")]),
                           ModelTurn(calls=[ToolCall("issuer", "get_issuer_material", "{}")]),
                           *(ModelTurn(calls=[ToolCall(f"company{index}", "calculate_company_cases", json.dumps(call(copy.deepcopy(judgments))))])
                             for index, call in enumerate(company_calls)),
                           ModelTurn(calls=[ToolCall("comparison", "calculate_comparison", json.dumps(stock_comparison_judgments(bound)))]),
                           ModelTurn(answer=answer)])
    return model, TestClient(create_app(model=model, data=FakeDataProvider(), research=source)).post("/api/analyze", json=stock_request())


def quarterly_prose(judgments):
    judgments["cases"][1]["assumptions"] = ["Margins hold, with a high probability."]  # rejected: probabilities are not modeled
    return judgments


def test_one_rejected_company_cases_call_is_returned_to_the_model_to_correct(tmp_path):
    source = provider(tmp_path, Edgar(facts=companyfacts(ifrs("USD"))))
    model, response = analyze_with(source, [quarterly_prose, lambda judgments: judgments])
    assert response.status_code == 200, response.text
    assert [case["terminal_price"] for case in response.json()["stock"]["cases"]] == ["100", "200", "300"]
    rejection = json.dumps(model.requests[4])
    assert "calculate_company_cases was rejected" in rejection and "Correct the inputs" in rejection
    # A second rejection is not retried.
    _, again = analyze_with(provider(tmp_path, Edgar(facts=companyfacts(ifrs("USD")))), [quarterly_prose, quarterly_prose])
    assert again.status_code == 502
