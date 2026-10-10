"""CIBC issuer research from its Supplementary Financial Information workbooks, against fixed fixtures. No network."""

import asyncio
import copy
import io
import json
import zipfile
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

import httpx
import pytest
from fastapi.testclient import TestClient

from analyst import issuer_research
from analyst.api import create_app
from analyst.issuer_research import (
    CIBC,
    CIBC_RESULTS,
    CIBC_SITE,
    CibcAdapter,
    IssuerResearchProvider,
)
from analyst.providers import FakeDataProvider, ModelTurn, ScriptedModel, ToolCall
from analyst.research import ReviewedResearchProvider
from analyst.schemas import AnalysisRequest, CompanyResearch, Position
from analyst.sec_research import SecCache
from tests.test_analysis import recommendation, snapshot
from tests.test_stock import company_judgments, stock_comparison_judgments

FOLDER = "/content/dam/cibc-public-assets/about-cibc/investor-relations/pdfs/quarterly-results"
FILES = {"october25": "2025/sfi-october25-en.xlsx", "april26": "2026/sfi-april26-en.xlsx", "july26": "2026/sfi-july26-en.xlsx"}
MODIFIED = {"october25": "Thu, 04 Dec 2025 12:00:00 GMT", "april26": "Thu, 28 May 2026 12:00:00 GMT", "july26": "Thu, 27 Aug 2026 12:00:00 GMT"}
LINKS = {date(2025, 10, 31): {"url": "https://www.sedarplus.ca/csa-party/records/document.html?id=annual2025", "filed": "2025-12-04",
                              "title": "CIBC 2025 Annual Report (SEDAR+)"},
         date(2026, 7, 31): {"url": "https://www.sedarplus.ca/csa-party/records/document.html?id=q32026", "filed": "2026-08-27",
                             "title": "CIBC Report to Shareholders, Q3 2026 (SEDAR+)"},
         date(2026, 4, 30): {"url": "https://www.sedarplus.ca/csa-party/records/document.html?id=q22026", "filed": "2026-05-28",
                             "title": "CIBC Report to Shareholders, Q2 2026 (SEDAR+)"}}

# Figures as CIBC printed them: Pg 4 rows then Pg 5 rows. FY2025 is repeated in every later workbook.
LABELS4 = ["Total revenue", "Net income", "Reported diluted EPS", "Dividends", "Book value (1)",
           "Reported return on common shareholders' equity (1)(2)", "Weighted-average diluted", "End of period"]
FY2025 = ["29133", "8454", "8.57", "3.88", "62.33", "14.3%", "940675", "926614"], ["57760", "13.3%"]
QUARTERS = {
    "october25": (["7576", "2180", "2.2000000000000002", "0.97", "62.33", "14.1%", "935115", "926614"], ["57760", "13.3%"]),
    "april26": (["8006", "2465", "2.5299999999999998", "1.07", "63.77", "16.4%", "924297", "914773"], ["58335", "13.6%"]),
    "july26": (["8368", "2409", "2.4700000000000002", "1.07", "65.14", "15.2%", "919211", "907935"], ["59146", "13.4%"]),
}
HEADERS = {"october25": ("Q4/25", "October 31, 2025"), "april26": ("Q2/26", "April 30, 2026"), "july26": ("Q3/26", "July 31, 2026")}


def sheet(rows: dict[str, str]) -> str:
    cells = "".join(f'<c r="{ref}" t="inlineStr"><is><t>{escape(text)}</t></is></c>' for ref, text in rows.items())
    return f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row>{cells}</row></sheetData></worksheet>'


def workbook(name: str, *, fy=FY2025, quarter=None, extra4=None, cet1_label="Common Equity Tier 1 ratio", numeric=False) -> bytes:
    """A small SFI workbook laid out like CIBC's: the quarter in column D/E, FY2025 under '12M' in column R/S."""
    q4, q5 = quarter or QUARTERS[name]
    header, ended = HEADERS[name]
    as_number = lambda text: str(Decimal(text.rstrip("%")) / 100) if numeric and text.endswith("%") else text  # noqa: E731
    pg4 = {"A1": "FINANCIAL HIGHLIGHTS", "R3": "2025", "D4": header, "R4": "12M"}
    for index, label in enumerate(LABELS4):
        pg4 |= {f"B{10 + index}": label, f"D{10 + index}": as_number(q4[index]), f"R{10 + index}": as_number(fy[0][index])}
    pg4 |= extra4 or {}
    pg5 = {"S3": "2025", "E4": header, "S4": "12M", "B11": "Common shareholders' equity (1)", "E11": q5[0], "S11": fy[1][0],
           "C20": cet1_label, "E20": as_number(q5[1]), "S20": as_number(fy[1][1])}
    sheets = {"COV": {"A9": f"For the period ended {ended}"}, "Pg 4 FH": pg4, "Pg 5 FH Contd": pg5}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        names = list(sheets)
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                         'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                         + "".join(f'<sheet name="{escape(sheet_name)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, sheet_name in enumerate(names))
                         + "</sheets></workbook>")
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         + "".join(f'<Relationship Id="rId{i + 1}" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(names))) + "</Relationships>")
        for i, sheet_name in enumerate(names):
            archive.writestr(f"xl/worksheets/sheet{i + 1}.xml", sheet(sheets[sheet_name]))
    return buffer.getvalue()


class Site:
    """CIBC's quarterly-results page and the three workbooks. Every request is counted."""

    def __init__(self, **books: bytes) -> None:
        self.books = {"october25": workbook("october25", numeric=True,
                                            cet1_label="CET1 ratio"),
                      "april26": workbook("april26"), "july26": workbook("july26"), **books}
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url == CIBC_RESULTS:
            links = "".join(f'<a href="{FOLDER}/{path}">SFI</a>' for path in FILES.values())
            return httpx.Response(200, text=f'<html><body>{links}<a href="{FOLDER}/2026/q326report-en.pdf">Report</a></body></html>')
        for name, path in FILES.items():
            if url == f"{CIBC_SITE}{FOLDER}/{path}":
                return httpx.Response(200, content=self.books[name], headers={"Last-Modified": MODIFIED[name]})
        return httpx.Response(404)


POSITION = Position(id="p2", account_id="broker", kind="stock", currency="CAD", ticker="CM", listing="XTSE", company_id=CIBC,
                    company_name="Canadian Imperial Bank of Commerce", shares=Decimal(5))


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(issuer_research, "SEDAR_LINKS", dict(LINKS))


def adapter(tmp_path, site):
    return CibcAdapter(cache=SecCache(tmp_path / "issuers"), transport=httpx.MockTransport(site))


def research(tmp_path, site=None, as_of=date(2026, 9, 30)) -> CompanyResearch:
    return asyncio.run(adapter(tmp_path, site or Site()).company(POSITION, as_of))


def facts_of(result):
    return {fact.id: fact for fact in result.facts}


def test_quarterly_results_and_bank_metrics(tmp_path):
    facts = facts_of(research(tmp_path))
    assert facts["revenue-q-2026-07-31"].value == 8_368_000_000 and facts["revenue-q-2026-07-31"].period_start == date(2026, 5, 1)
    assert facts["net_income-q-2026-07-31"].value == 2_409_000_000
    assert (facts["eps_diluted-q-2026-07-31"].value, facts["eps_diluted-q-2026-07-31"].unit) == (Decimal("2.47"), "per_share")
    assert facts["dividend_per_share-q-2026-07-31"].value == Decimal("1.07")
    assert facts["book_value_per_share-at-2026-07-31"].value == Decimal("65.14")
    assert facts["book_value-at-2026-07-31"].value == 59_146_000_000 and facts["book_value-at-2026-07-31"].period_start is None
    assert facts["shares-at-2026-07-31"].value == 907_935_000 and facts["shares-at-2026-07-31"].unit == "shares"
    assert facts["weighted_diluted_shares-q-2026-07-31"].value == 919_211_000
    assert (facts["roe-q-2026-07-31"].value, facts["roe-q-2026-07-31"].unit) == (Decimal("0.152"), "ratio")
    assert facts["cet1_ratio-at-2026-07-31"].value == Decimal("0.134")
    assert "annualized by CIBC" in facts["roe-q-2026-07-31"].definition
    # Bank figures only: no industrial cash flow, free cash flow, cash or debt is invented.
    assert not {fact.metric for fact in facts.values()} & {"free_cash_flow", "operating_cash_flow", "capex", "cash", "total_debt"}


def test_annual_report_figures_from_the_fiscal_year_end_workbook(tmp_path):
    result = research(tmp_path)
    facts = facts_of(result)
    revenue = facts["revenue-fy-2025-10-31"]
    assert (revenue.value, revenue.period_start, revenue.period_end) == (29_133_000_000, date(2024, 11, 1), date(2025, 10, 31))
    assert revenue.document_ids == ["issuer-cibc-sfi-2025-10-31", "sedar-cibc-2025-10-31"]
    assert facts["eps_diluted-fy-2025-10-31"].value == Decimal("8.57") and facts["dividend_per_share-fy-2025-10-31"].value == Decimal("3.88")
    # The October workbook stores percentages as fractions and calls CET1 "CET1 ratio"; both read the same.
    assert facts["roe-fy-2025-10-31"].value == Decimal("0.143") and facts["cet1_ratio-at-2025-10-31"].value == Decimal("0.133")
    assert facts["book_value-at-2025-10-31"].value == 57_760_000_000 and facts["book_value_per_share-at-2025-10-31"].value == Decimal("62.33")
    # Both periods kept and labelled; nothing is annualized from a quarter.
    assert {fact.id.split("-")[1] for fact in result.facts} == {"q", "fy", "at"}
    assert {doc.id for doc in result.documents} == {"issuer-cibc-sfi-2026-07-31", "sedar-cibc-2026-07-31", "issuer-cibc-sfi-2025-10-31",
                                                    "sedar-cibc-2025-10-31"}
    assert result.sector == "financial" and result.issues == []


def test_latest_quarter_depends_on_the_decision_date(tmp_path):
    facts = facts_of(research(tmp_path, as_of=date(2026, 7, 15)))  # July's workbook does not exist yet
    assert "revenue-q-2026-04-30" in facts and "revenue-q-2026-07-31" not in facts
    assert facts["revenue-q-2026-04-30"].period_start == date(2026, 2, 1)
    facts = facts_of(research(tmp_path / "before", as_of=date(2026, 8, 10)))  # quarter ended, workbook not yet published
    assert "revenue-q-2026-04-30" in facts and "revenue-fy-2025-10-31" in facts


def test_source_provenance(tmp_path):
    for fact in research(tmp_path).facts:
        source = fact.sources[0]
        assert fact.review == "issuer_report" and not fact.notes_checked and fact.filing_checked
        assert source.url.startswith(f"{CIBC_SITE}{FOLDER}/") and source.taxonomy == "cibc-sfi"
        assert (source.period_end, source.accession) == (fact.period_end, source.url.rsplit("/", 1)[1])
    bvps = facts_of(research(tmp_path)).get("book_value_per_share-at-2026-07-31")
    source = bvps.sources[0]
    assert (source.concept, source.unit, source.value, source.fiscal_period, source.fiscal_year, source.filed) == (
        "Pg 4 FH!D14 Book value", "CAD per share", Decimal("65.14"), "Q3", 2026, date(2026, 8, 27))
    assert "Pg 4 FH D14 ('Book value')" in bvps.definition and bvps.currency == "CAD"
    equity = facts_of(research(tmp_path))["book_value-at-2026-07-31"].sources[0]
    assert (equity.concept, equity.unit, equity.value) == ("Pg 5 FH Contd!E11 Common shareholders' equity", "CAD millions", 59146)


def test_duplicate_label_and_missing_row_stay_unknown(tmp_path):
    duplicate = workbook("july26", extra4={"B30": "Dividends", "D30": "9.99", "R30": "9.99"})
    result = research(tmp_path, Site(july26=duplicate))
    facts = facts_of(result)
    assert "dividend_per_share-q-2026-07-31" not in facts and "revenue-q-2026-07-31" in facts
    assert any("'Dividends' appears on 2 rows" in issue for issue in result.issues)
    missing = workbook("july26", quarter=(["8368", "2409", "2.47", "1.07", "65.14", "n/a", "919211", "907935"], ["59146", "13.4%"]))
    result = research(tmp_path / "missing", Site(july26=missing))
    assert "roe-q-2026-07-31" not in facts_of(result)
    assert any(issue.startswith("roe for the quarter: sfi-july26-en.xlsx Pg 4 FH D15 is 'n/a'") for issue in result.issues)


def test_restated_annual_figure_stays_unknown(tmp_path):
    restated = workbook("july26", fy=(["29100", *FY2025[0][1:]], FY2025[1]))
    result = research(tmp_path, Site(july26=restated))
    assert "revenue-fy-2025-10-31" not in facts_of(result) and "net_income-fy-2025-10-31" in facts_of(result)
    assert any("revenue for fiscal 2025: sfi-october25-en.xlsx reports 29133 but sfi-july26-en.xlsx reports 29100" in issue for issue in result.issues)


def test_common_equity_must_reproduce_reported_book_value_per_share(tmp_path):
    # 59,146m / 907.935m = 65.14: consistent. 59,500m would be 65.53 against a reported 65.14: definitions differ.
    assert "book_value-at-2026-07-31" in facts_of(research(tmp_path))
    odd = workbook("july26", quarter=(QUARTERS["july26"][0], ["59500", "13.4%"]))
    result = research(tmp_path / "odd", Site(july26=odd))
    assert "book_value-at-2026-07-31" not in facts_of(result)
    assert any("is 65.53, not the reported book value per share 65.14" in issue for issue in result.issues)


def test_unpinned_sedar_link_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(issuer_research, "SEDAR_LINKS", {date(2025, 10, 31): LINKS[date(2025, 10, 31)]})
    result = research(tmp_path)
    assert facts_of(result)["revenue-q-2026-07-31"].document_ids == ["issuer-cibc-sfi-2026-07-31"]
    assert any("SEDAR+ verification link not pinned for CIBC's filing for the period ended 2026-07-31" in issue for issue in result.issues)


def test_cache_reuse_makes_no_repeat_requests(tmp_path):
    site = Site()
    research(tmp_path, site)
    assert site.requests and len(site.requests) == 3  # the page, July's and October's workbooks
    site.requests = []
    again = research(tmp_path, site)
    assert site.requests == [] and facts_of(again)["revenue-q-2026-07-31"].value == 8_368_000_000


class Fallback:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def company(self, position, as_of):
        self.calls.append(position.id)
        return CompanyResearch(company_id=position.company_id or position.id, sector="unknown", cyclical=None, documents=[], facts=[], issues=[])


def test_other_companies_go_to_the_existing_provider(tmp_path):
    fallback, site = Fallback(), Site()
    provider = IssuerResearchProvider(fallback, {CIBC: adapter(tmp_path, site)})
    us = POSITION.model_copy(update={"id": "us", "company_id": "CIK0000320193", "listing": "XNAS", "currency": "USD"})
    cm_on_nyse = POSITION.model_copy(update={"id": "cm-us", "listing": "XNYS", "currency": "USD"})
    for position in (us, cm_on_nyse):
        asyncio.run(provider.company(position, date(2026, 9, 30)))
    assert fallback.calls == ["us", "cm-us"] and site.requests == []
    assert asyncio.run(provider.company(POSITION, date(2026, 9, 30))).facts and fallback.calls == ["us", "cm-us"]


def cm_request():
    portfolio = snapshot()
    portfolio["positions"][1].update(company_id=CIBC, ticker="CM", company_name="Canadian Imperial Bank of Commerce")
    return {"question": "What do you think about CM?", "portfolio": portfolio, "stock": {"position_id": "p2"}}


def bank_judgments(evidence=None):
    paths = company_judgments()
    paths.update(method="book_exit", revenue_fact_id=None, metric_fact_id="book_value-at-2026-07-31", shares_fact_id="shares-at-2026-07-31",
                 mid_cycle_context="Credit losses are near a mid-cycle level rather than a benign trough.")
    for case, multiple in zip(paths["cases"], ("1", "1.5", "2"), strict=True):
        # The growth sent here is ignored for a bank: Python derives it from ROE and payout.
        case.update(growth=["0.3"] * 5, margins=["1"] * 5, payout=["0.45"] * 5, exit_multiple=multiple, exit_sensitivity=[multiple] * 3,
                    return_on_equity=["0.14"] * 5, discount_rate="0.1")
    return paths


def analyze_cm(source, evidence_ids):
    request = cm_request()
    answer = copy.deepcopy(recommendation())
    answer.update(preferred_action="hold", reason="Hold conditionally while the bank cases are weighed against existing concentration.",
                  alternatives=[{"action": "no_action", "reason": "Retaining the snapshot avoids an unconfirmed transaction."}],
                  evidence_ids=evidence_ids)
    bound = AnalysisRequest.model_validate(request).model_dump(mode="json")
    model = ScriptedModel([ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
                           ModelTurn(calls=[ToolCall("sedar", "get_sedar_filings", "{}")]),
                           ModelTurn(calls=[ToolCall("issuer", "get_issuer_material", "{}")]),
                           ModelTurn(calls=[ToolCall("company", "calculate_company_cases", json.dumps(bank_judgments()))]),
                           ModelTurn(calls=[ToolCall("comparison", "calculate_comparison", json.dumps(stock_comparison_judgments(bound)))]),
                           ModelTurn(answer=answer)])
    response = TestClient(create_app(model=model, data=FakeDataProvider(), research=source)).post("/api/analyze", json=request)
    assert response.status_code == 200, response.text
    return response.json()


def worked_example(multiple):
    """Independently, in floats: start 59,146m / 907.935m = 65.1434 per share. Each year earns 14% on opening book,
    pays 45% of it and retains 55%, so book grows 0.14 x 0.55 = 7.7% a year. Exit at multiple x year-5 book,
    discounted at 10%, plus each year's dividend discounted."""
    book, dividends = 59_146 / 907.935, 0.0
    for year in range(1, 6):
        dividends += book * 0.14 * 0.45 / 1.1 ** year
        book *= 1.077
    return book, book * multiple, book * multiple / 1.1 ** 5 + dividends


@pytest.mark.parametrize("pinned_link", [True, False])
def test_cm_bank_valuation_derives_book_growth_from_roe_and_payout(tmp_path, monkeypatch, pinned_link):
    if not pinned_link:
        monkeypatch.setattr(issuer_research, "SEDAR_LINKS", {})  # SEDAR+ is optional verification, never required
    source = IssuerResearchProvider(ReviewedResearchProvider(), {CIBC: adapter(tmp_path, Site())})
    evidence = ["sedar-cibc-2026-07-31", "issuer-cibc-sfi-2026-07-31"] if pinned_link else ["issuer-cibc-sfi-2026-07-31"]
    result = analyze_cm(source, evidence)
    stock = result["stock"]
    base = stock["cases"][1]
    assert base["starting_per_share"] == "65.1434"
    assert [(year["return_on_equity"], year["payout"], year["retention"], year["book_growth"]) for year in base["path"]] == [("0.14", "0.45", "0.55", "0.077")] * 5
    for case, multiple in zip(stock["cases"], (1, 1.5, 2), strict=True):
        book, price, present = worked_example(multiple)
        assert abs(Decimal(case["path"][-1]["metric_per_share"]) - Decimal(str(book))) < Decimal("0.0001")
        assert abs(Decimal(case["terminal_price"]) - Decimal(str(price))) < Decimal("0.0001")
        assert abs(Decimal(case["present_value_per_share"]) - Decimal(str(present))) < Decimal("0.0001")
    assert any("supplied growth path is not used" in note for note in base["qualifications"])
    # 3.88 / 8.57 = 45.3%: the reported fiscal-2025 payout the payout judgment is anchored to.
    assert any("Reported payout for the year ended 2025-10-31: dividends 3.88 / diluted EPS 8.57 = 45.3%" in note for note in stock["valuation"]["notes"])
    assert any("read automatically from the issuer's published report" in note for note in base["qualifications"])
    assert result["recommendation"]["preferred_action"] == "hold"
    issues = " ".join(stock["research"]["issues"])
    assert ("SEDAR+ verification link not pinned" in issues) is not pinned_link


def test_bank_case_without_roe_stays_unknown(tmp_path):
    paths = bank_judgments()
    for case in paths["cases"]:
        case["return_on_equity"] = None
    source = IssuerResearchProvider(ReviewedResearchProvider(), {CIBC: adapter(tmp_path, Site())})
    request = cm_request()
    bound = AnalysisRequest.model_validate(request).model_dump(mode="json")
    answer = copy.deepcopy(recommendation())
    answer.update(preferred_action="hold", reason="Hold conditionally while the bank cases are weighed.", evidence_ids=["issuer-cibc-sfi-2026-07-31"],
                  alternatives=[{"action": "no_action", "reason": "Retaining the snapshot avoids an unconfirmed transaction."}])
    model = ScriptedModel([ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
                           ModelTurn(calls=[ToolCall("sedar", "get_sedar_filings", "{}")]),
                           ModelTurn(calls=[ToolCall("issuer", "get_issuer_material", "{}")]),
                           ModelTurn(calls=[ToolCall("company", "calculate_company_cases", json.dumps(paths))]),
                           ModelTurn(calls=[ToolCall("comparison", "calculate_comparison", json.dumps(stock_comparison_judgments(bound)))]),
                           ModelTurn(answer=answer)])
    stock = TestClient(create_app(model=model, data=FakeDataProvider(), research=source)).post("/api/analyze", json=request).json()["stock"]
    assert all(case["terminal_price"] is None for case in stock["cases"])
    assert "book-value growth and distributable earnings cannot be derived" in str(stock["cases"][1]["qualifications"])
