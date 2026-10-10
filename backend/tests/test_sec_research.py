"""Live SEC research for verified US stocks, against fixed EDGAR fixtures. No test touches the network."""

import asyncio
import copy
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.providers import FakeDataProvider, ModelTurn, ScriptedModel, ToolCall
from analyst.research import ReviewedResearchProvider
from analyst.schemas import AnalysisRequest, CompanyResearch, Position
from analyst.sec_research import SecCache, SecResearchProvider
from tests.test_analysis import snapshot
from tests.test_stock import (
    company_judgments,
    research_fixture,
    stock_answer,
    stock_comparison_judgments,
)

CIK = 123456
COMPANY = f"CIK{CIK:010d}"
AGENT = "Test test@example.test"
TEN_K, TEN_Q, OLD_Q, RESULTS, BOARD = "0000123456-26-000010", "0000123456-26-000030", "0000123456-25-000050", "0000123456-26-000029", "0000123456-26-000020"
FACTS_URL = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK:010d}.json"
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{CIK:010d}.json"
FOLDER = f"https://www.sec.gov/Archives/edgar/data/{CIK}/{RESULTS.replace('-', '')}"


def row(value, end, accn, form, fp, start=None, filed=None, fy=2025):
    return {**({"start": start} if start else {}), "end": end, "val": value, "accn": accn, "fy": fy, "fp": fp, "form": form,
            "filed": filed or {TEN_K: "2026-02-20", TEN_Q: "2026-08-05", OLD_Q: "2025-11-05"}[accn]}


def annual(*values):
    """Three fiscal years from the 10-K, newest first."""
    years = [("2025-01-01", "2025-12-31"), ("2024-01-01", "2024-12-31"), ("2023-01-01", "2023-12-31")]
    return [row(value, end, TEN_K, "10-K", "FY", start=start) for value, (start, end) in zip(values, years, strict=False)]


QUARTER, YTD = ("2026-04-01", "2026-06-30"), ("2026-01-01", "2026-06-30")


def companyfacts():
    usd = lambda rows: {"units": {"USD": rows}}  # noqa: E731
    gaap = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": usd([
            *annual(1_000_000_000, 900_000_000, 800_000_000),
            row(280_000_000, QUARTER[1], TEN_Q, "10-Q", "Q2", start=QUARTER[0], fy=2026),
            row(540_000_000, YTD[1], TEN_Q, "10-Q", "Q2", start=YTD[0], fy=2026),
            # The same quarter as filed earlier in an older 10-Q, and a duplicate row: both must be handled.
            row(250_000_000, "2025-09-30", OLD_Q, "10-Q", "Q3", start="2025-07-01"),
            row(1_000_000_000, "2025-12-31", TEN_K, "10-K", "FY", start="2025-01-01"),
        ]),
        "Revenues": usd(annual(1_000_000_000)),  # an agreeing alternative tag
        "OperatingIncomeLoss": usd([*annual(200_000_000, 150_000_000, 120_000_000),
                                    row(70_000_000, QUARTER[1], TEN_Q, "10-Q", "Q2", start=QUARTER[0], fy=2026)]),
        "NetIncomeLoss": usd([*annual(150_000_000, -20_000_000, 90_000_000),
                              row(55_000_000, QUARTER[1], TEN_Q, "10-Q", "Q2", start=QUARTER[0], fy=2026)]),
        "NetCashProvidedByUsedInOperatingActivities": usd([*annual(260_000_000, 210_000_000),
                                                           row(140_000_000, YTD[1], TEN_Q, "10-Q", "Q2", start=YTD[0], fy=2026)]),
        "PaymentsToAcquirePropertyPlantAndEquipment": usd([*annual(60_000_000, 50_000_000),
                                                           row(30_000_000, YTD[1], TEN_Q, "10-Q", "Q2", start=YTD[0], fy=2026)]),
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
            *annual(100_000_000, 98_000_000, 97_000_000),
            row(101_000_000, QUARTER[1], TEN_Q, "10-Q", "Q2", start=QUARTER[0], fy=2026)]}},
        "CashAndCashEquivalentsAtCarryingValue": usd([row(300_000_000, "2025-12-31", TEN_K, "10-K", "FY"),
                                                      row(320_000_000, "2026-06-30", TEN_Q, "10-Q", "Q2", fy=2026)]),
        "LongTermDebt": usd([row(500_000_000, "2025-12-31", TEN_K, "10-K", "FY")]),
        "CommercialPaper": usd([row(50_000_000, "2025-12-31", TEN_K, "10-K", "FY")]),
        "LongTermDebtNoncurrent": usd([row(450_000_000, "2026-06-30", TEN_Q, "10-Q", "Q2", fy=2026)]),
        "LongTermDebtCurrent": usd([row(40_000_000, "2026-06-30", TEN_Q, "10-Q", "Q2", fy=2026)]),
    }
    return {"cik": CIK, "entityName": "Example Devices Inc.", "facts": {"us-gaap": gaap}}


def submissions():
    filings = [("10-Q", "2026-08-05", "2026-06-30", TEN_Q, "exd-20260630.htm", ""),
               ("8-K", "2026-07-30", "2026-07-30", RESULTS, "exd-20260730.htm", "2.02,9.01"),
               ("8-K", "2026-05-10", "2026-05-08", BOARD, "exd-20260508.htm", "5.02"),
               ("10-K", "2026-02-20", "2025-12-31", TEN_K, "exd-20251231.htm", ""),
               ("10-Q", "2025-11-05", "2025-09-30", OLD_Q, "exd-20250930.htm", "")]
    keys = ("form", "filingDate", "reportDate", "accessionNumber", "primaryDocument", "items")
    return {"cik": str(CIK), "name": "Example Devices Inc.", "sic": "3674", "sicDescription": "Semiconductors",
            "filings": {"recent": {key: [filing[index] for filing in filings] for index, key in enumerate(keys)}}}


RELEASE = ("<html><head><style>p{color:red}</style><script>ignored()</script></head><body>"
           "<p>Example Devices reports second-quarter revenue of $280&nbsp;million.</p></body></html>")


class Edgar:
    """The SEC endpoints the provider uses. Every request is counted; `down` simulates an outage."""

    def __init__(self, facts=None, filings=None) -> None:
        self.facts, self.filings = facts or companyfacts(), filings or submissions()
        self.requests: list[str] = []
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"] == AGENT
        self.requests.append(str(request.url))
        if self.down:
            return httpx.Response(503)
        pages = {FACTS_URL: self.facts, SUBMISSIONS_URL: self.filings,
                 f"{FOLDER}/index.json": {"directory": {"item": [{"name": "exd-20260730.htm"}, {"name": "exd-ex991.htm"}]}}}
        if str(request.url) == f"{FOLDER}/exd-ex991.htm":
            return httpx.Response(200, text=RELEASE)
        return httpx.Response(200, json=pages[str(request.url)]) if str(request.url) in pages else httpx.Response(404)


def provider(tmp_path, edgar: Edgar, reviewed=None) -> SecResearchProvider:
    return SecResearchProvider(agent=AGENT, cache=SecCache(tmp_path / "sec"), transport=httpx.MockTransport(edgar), reviewed=reviewed)


POSITION = Position(id="p1", account_id="tfsa", kind="stock", currency="USD", ticker="EXD", listing="XNAS",
                    company_id=COMPANY, company_name="Example Devices Inc.", shares=Decimal(10))


def research(tmp_path, edgar=None, as_of=date(2026, 9, 30), reviewed=None) -> CompanyResearch:
    return asyncio.run(provider(tmp_path, edgar or Edgar(), reviewed).company(POSITION, as_of))


def facts_of(result: CompanyResearch) -> dict:
    return {fact.id: fact for fact in result.facts}


def test_normal_10k_and_10q_company(tmp_path):
    result = research(tmp_path)
    assert (result.company_id, result.sector, result.cyclical) == (COMPANY, "industrial", None)
    facts = facts_of(result)
    assert facts["revenue-fy-2025-12-31"].value == 1_000_000_000
    assert [facts[f"revenue-fy-{year}-12-31"].value for year in (2024, 2023)] == [900_000_000, 800_000_000]
    assert facts["net_income-fy-2024-12-31"].value == -20_000_000  # a loss is kept as reported
    assert facts["shares-fy-2025-12-31"].value == 100_000_000 and facts["shares-fy-2025-12-31"].unit == "shares"
    assert facts["revenue-q-2026-06-30"].value == 280_000_000
    docs = {doc.id: doc for doc in result.documents}
    assert list(docs) == [f"sec-10-k-{TEN_K}", f"sec-10-q-{TEN_Q}", f"issuer-8k-{RESULTS}", f"sec-8k-{BOARD}"]
    release = docs[f"issuer-8k-{RESULTS}"]
    assert (release.authority, release.available, release.url) == ("issuer", True, f"{FOLDER}/exd-ex991.htm")
    assert release.excerpt == "Example Devices reports second-quarter revenue of $280 million."  # tags, script and style gone
    assert "Item 5.02 Director or officer changes" in docs[f"sec-8k-{BOARD}"].title
    assert "revenue 2025-01-01 to 2025-12-31: 1,000,000,000 USD" in docs[f"sec-10-k-{TEN_K}"].excerpt
    assert all(f"{OLD_Q}" not in doc.id for doc in result.documents)  # the 10-Q before the 10-K is superseded
    assert result.issues == []


def test_missing_xbrl_concept_stays_unknown(tmp_path):
    facts = companyfacts()
    del facts["facts"]["us-gaap"]["NetIncomeLoss"]
    result = research(tmp_path, Edgar(facts=facts))
    assert not [fact for fact in result.facts if fact.metric == "net_income"]
    assert any(issue.startswith("net_income for the period ending 2025-12-31: not reported under NetIncomeLoss, ProfitLoss") for issue in result.issues)
    assert facts_of(result)["revenue-fy-2025-12-31"].value == 1_000_000_000


def test_duplicate_and_alternative_tags(tmp_path):
    facts = companyfacts()
    # Agreeing duplicates and an agreeing alternative are fine; a disagreeing alternative makes that period unusable.
    facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(annual(1_000_000_000, 905_000_000)[1])
    result = research(tmp_path, Edgar(facts=facts))
    by_id = facts_of(result)
    assert by_id["revenue-fy-2025-12-31"].custom_tags_checked is True
    assert by_id["revenue-fy-2025-12-31"].sources[0].concept == "Revenues"  # the preferred tag
    assert by_id["revenue-fy-2024-12-31"].custom_tags_checked is False
    assert any("revenue for the period ending 2024-12-31: alternative or duplicate XBRL tags disagree" in issue for issue in result.issues)
    assert len([fact for fact in result.facts if fact.metric == "revenue" and fact.period_end == date(2025, 12, 31)]) == 1


def test_annual_and_quarterly_periods(tmp_path):
    by_id = facts_of(research(tmp_path))
    fiscal = by_id["revenue-fy-2025-12-31"]
    assert (fiscal.period_start, fiscal.period_end) == (date(2025, 1, 1), date(2025, 12, 31))
    quarter = by_id["revenue-q-2026-06-30"]
    assert (quarter.period_start, quarter.period_end, quarter.document_ids) == (date(2026, 4, 1), date(2026, 6, 30), [f"sec-10-q-{TEN_Q}"])
    # Cash flows in a 10-Q are year to date only; no 3-month cash flow is invented.
    assert "operating_cash_flow-ytd-2026-06-30" in by_id and "operating_cash_flow-q-2026-06-30" not in by_id
    assert "revenue-q-2025-09-30" not in by_id
    # Before the 10-Q was filed, only the 10-K is used.
    early = research(tmp_path / "early", as_of=date(2026, 7, 1))
    assert {doc.id for doc in early.documents} == {f"sec-10-k-{TEN_K}", f"sec-8k-{BOARD}"}


def test_cash_debt_and_free_cash_flow(tmp_path):
    by_id = facts_of(research(tmp_path))
    assert by_id["cash-at-2025-12-31"].value == 300_000_000
    debt = by_id["total_debt-at-2025-12-31"]
    assert debt.value == 550_000_000 and [s.concept for s in debt.sources] == ["LongTermDebt", "CommercialPaper"]
    assert by_id["total_debt-at-2026-06-30"].value == 490_000_000  # non-current plus current maturities
    fcf = by_id["free_cash_flow-fy-2025-12-31"]
    assert fcf.value == 200_000_000 and [s.concept for s in fcf.sources] == ["NetCashProvidedByUsedInOperatingActivities", "PaymentsToAcquirePropertyPlantAndEquipment"]
    assert by_id["free_cash_flow-ytd-2026-06-30"].value == 110_000_000
    assert "free_cash_flow-fy-2023-12-31" not in by_id  # no 2023 cash flow reported, so no FCF


def test_source_provenance(tmp_path):
    result = research(tmp_path)
    for fact in result.facts:
        assert fact.review == "sec_xbrl" and fact.filing_checked and fact.segments_checked and not fact.notes_checked
        assert fact.sources and all(source.url == FACTS_URL and source.taxonomy == "us-gaap" for source in fact.sources)
        assert all(source.accession in fact.definition for source in fact.sources)
    revenue = facts_of(result)["revenue-fy-2025-12-31"].sources[0]
    assert (revenue.accession, revenue.form, revenue.fiscal_period, revenue.filed, revenue.value) == (TEN_K, "10-K", "FY", date(2026, 2, 20), 1_000_000_000)
    assert all(doc.url.startswith(f"https://www.sec.gov/Archives/edgar/data/{CIK}/") for doc in result.documents)


def test_sec_outage_with_cached_data(tmp_path):
    edgar = Edgar()
    research(tmp_path, edgar)
    cache = SecCache(tmp_path / "sec")
    for key in (f"companyfacts/CIK{CIK:010d}.json", f"submissions/CIK{CIK:010d}.json"):
        path = cache.directory / key
        stored = json.loads(path.read_text())
        stored["fetched_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()  # yesterday: due for a refresh
        path.write_text(json.dumps(stored))
    edgar.down, edgar.requests = True, []
    result = research(tmp_path, edgar)
    assert len(edgar.requests) == 2  # it tried, then fell back
    assert facts_of(result)["revenue-fy-2025-12-31"].value == 1_000_000_000
    assert any("SEC could not be reached (HTTPStatusError); using data cached" in issue for issue in result.issues)


def test_sec_outage_without_cached_data(tmp_path):
    edgar = Edgar()
    edgar.down = True
    result = research(tmp_path, edgar)
    assert (result.documents, result.facts, result.sector) == ([], [], "unknown")
    assert result.issues == [f"SEC could not be reached and nothing is cached for submissions/CIK{CIK:010d}.json. Company facts remain unknown."]


def test_reviewed_file_is_optional_extra_evidence(tmp_path):
    record = research_fixture()
    record = json.loads(json.dumps(record).replace('"acme"', json.dumps(COMPANY)))
    reviewed = ReviewedResearchProvider({COMPANY: CompanyResearch.model_validate(record)})
    result = research(tmp_path, reviewed=reviewed)
    assert {"filing", "issuer", f"sec-10-k-{TEN_K}"} <= {doc.id for doc in result.documents}
    assert {"revenue", "revenue-fy-2025-12-31"} <= set(facts_of(result))
    # Anything that isn't a verified US stock is left to the reviewed file, as before.
    canadian = POSITION.model_copy(update={"listing": "XTSE", "currency": "CAD"})
    edgar = Edgar()
    assert asyncio.run(provider(tmp_path, edgar).company(canadian, date(2026, 9, 30))).documents == [] and edgar.requests == []


def stock_request():
    portfolio = json.loads(json.dumps(snapshot()).replace('"acme"', json.dumps(COMPANY)))
    return {"question": "What do you think about ACME?", "portfolio": portfolio, "stock": {"position_id": "p1"}}


def analyze(source: SecResearchProvider):
    judgments = company_judgments()
    judgments.update(revenue_fact_id="revenue-fy-2025-12-31", shares_fact_id="shares-fy-2025-12-31")
    answer = copy.deepcopy(stock_answer())
    answer["evidence_ids"] = [f"sec-10-k-{TEN_K}", f"issuer-8k-{RESULTS}"]
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


def test_stock_analysis_uses_sec_facts_and_repeats_with_zero_sec_requests(tmp_path):
    edgar = Edgar()
    source = provider(tmp_path, edgar)
    first = analyze(source)
    stock = first["stock"]
    # 1,000m revenue x 10% margin x 10 exit multiple / 100m diluted shares: Python arithmetic on SEC facts.
    assert [case["terminal_price"] for case in stock["cases"]] == ["5", "10", "15"]
    assert first["recommendation"]["preferred_action"] == "hold"
    assert any("automated SEC XBRL fact (revenue-fy-2025-12-31)" in note for note in stock["cases"][1]["qualifications"])
    assert len(edgar.requests) == 4  # submissions, company facts, the 8-K index and its exhibit

    edgar.requests = []
    again = analyze(provider(tmp_path, edgar))
    assert edgar.requests == [] and again["stock"]["cases"] == stock["cases"]


def test_share_count_that_looks_like_a_split_stays_unknown(tmp_path):
    facts = companyfacts()
    cover = lambda count: {"EntityCommonStockSharesOutstanding": {"units": {"shares": [  # noqa: E731
        {"end": "2026-07-31", "val": count, "accn": TEN_Q, "form": "10-Q", "fp": "Q2", "fy": 2026, "filed": "2026-08-05"}]}}}
    facts["facts"]["dei"] = cover(99_500_000)  # in line with ~100m diluted shares: kept
    assert "shares-fy-2025-12-31" in facts_of(research(tmp_path / "ok", Edgar(facts=facts)))
    facts["facts"]["dei"] = cover(1_000_000_000)  # ten times as many shares outstanding: a 10-for-1 split since
    result = research(tmp_path / "split", Edgar(facts=facts))
    assert not [fact for fact in result.facts if fact.metric == "shares"]
    assert any("looks like a stock split" in issue for issue in result.issues)
    assert facts_of(result)["revenue-fy-2025-12-31"].value == 1_000_000_000
