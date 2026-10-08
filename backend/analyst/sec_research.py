"""Live SEC EDGAR research for verified US stocks: XBRL company facts, filing history, 10-K, 10-Q and recent 8-Ks.

SEC is the primary source. Every fact keeps its tag, accession, form, filing date and URL; a fact SEC did not report
stays unknown and is never substituted. Derived figures (free cash flow, total debt from parts) are plain arithmetic
here. Responses are cached on disk: company-wide files for a day, filing files for good, so repeated analyses do not
call SEC again, and an SEC outage falls back to what is cached.
"""

import html
import json
import os
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from .research import ReviewedResearchProvider
from .schemas import (
    US_LISTINGS,
    CompanyResearch,
    FactSource,
    Position,
    ResearchDocument,
    ResearchFact,
)

DATA_URL = "https://data.sec.gov"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
FACTS_URL = DATA_URL + "/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = DATA_URL + "/submissions/CIK{cik:010d}.json"

# Standard us-gaap concepts per metric, in order of preference. Only one per period is used; a disagreeing
# alternative makes that period's figure unusable rather than picking between them.
CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",
                            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
}
DEFINITIONS = {
    "revenue": "Total revenue as reported", "operating_income": "Operating income (loss) as reported",
    "net_income": "Net income (loss) as reported", "operating_cash_flow": "Net cash from operating activities as reported",
    "capex": "Payments to acquire property, plant and equipment as reported", "shares": "Weighted-average diluted shares outstanding",
    "cash": "Cash and cash equivalents at period end",
}
DURATION = ("revenue", "operating_income", "net_income", "operating_cash_flow", "capex", "shares")
CASH_FLOW = ("operating_cash_flow", "capex")
# Total debt: the combined figure when reported, else long-term debt (including current maturities) plus short-term
# borrowings or commercial paper when reported at the same date. Any missing required part leaves it unknown.
DEBT_TOTAL = "DebtLongtermAndShorttermCombinedAmount"
DEBT_SHORT = ("ShortTermBorrowings", "CommercialPaper")
ITEMS = {"1.01": "Entry into a material agreement", "1.02": "Termination of a material agreement", "2.01": "Acquisition or disposition of assets",
         "2.02": "Results of operations", "2.03": "New direct financial obligation", "2.05": "Exit or disposal costs", "2.06": "Material impairments",
         "3.02": "Unregistered equity sales", "5.02": "Director or officer changes", "5.07": "Shareholder vote", "7.01": "Regulation FD disclosure",
         "8.01": "Other events", "9.01": "Financial statements and exhibits"}
RELEVANT_8K = {"1.01", "1.02", "2.01", "2.02", "2.03", "2.05", "2.06", "3.02", "5.02", "7.01", "8.01"}
EXCERPT = 6000


def sector_of(sic: str | None) -> str:
    """SEC's SIC code to the valuation sector the calculator expects. Unknown stays unknown."""
    if not sic or not sic.isdigit():
        return "unknown"
    code = int(sic)
    if code == 6798:
        return "reit"
    if 6000 <= code <= 6411 or 6700 <= code <= 6799:
        return "financial"
    return "industrial" if 2000 <= code <= 3999 else "other"


def text_of(page: str) -> str:
    """Readable text of an HTML exhibit: scripts, styles and tags removed, whitespace collapsed."""
    page = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page)
    page = re.sub(r"(?s)<[^>]+>", " ", page)
    return re.sub(r"\s+", " ", html.unescape(page)).strip()


class SecCache:
    """SEC responses on disk, keyed by CIK or accession, with the time they were fetched."""

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory or os.environ.get("SEC_CACHE_DIR") or Path(__file__).resolve().parents[1] / "data" / "sec")

    def get(self, key: str) -> tuple[Any, datetime] | None:
        path = self.directory / key
        if not path.exists():
            return None
        stored = json.loads(path.read_text(encoding="utf-8"))
        return stored["body"], datetime.fromisoformat(stored["fetched_at"])

    def put(self, key: str, body: Any) -> None:
        path = self.directory / key
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"fetched_at": datetime.now(UTC).isoformat(), "body": body}), encoding="utf-8")
        temporary.replace(path)


class SecUnavailable(Exception):
    """SEC could not be reached and nothing is cached for this request."""


class SecResearchProvider:
    """SEC first for a verified US stock (company ID CIK##########); the reviewed reference file is optional extra
    evidence. Anything else falls through to the reviewed file unchanged."""

    def __init__(self, *, agent: str, cache: SecCache | None = None, reviewed: ReviewedResearchProvider | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.agent = agent
        self.cache = cache or SecCache()
        self.reviewed = reviewed or ReviewedResearchProvider()
        self.transport = transport

    @classmethod
    def from_environment(cls) -> "SecResearchProvider":
        return cls(agent=os.environ.get("SEC_USER_AGENT", ""), reviewed=ReviewedResearchProvider.from_environment())

    async def _fetch(self, key: str, url: str, *, daily: bool, text: bool = False) -> tuple[Any, str | None]:
        """A cached SEC response. Daily files are re-fetched once a day; on an outage the cached copy is used and
        the returned note says so. Filing files never change and are fetched once."""
        cached = self.cache.get(key)
        if cached and (not daily or cached[1].date() == datetime.now(UTC).date()):
            return cached[0], None
        try:
            if not self.agent:
                raise httpx.HTTPError("SEC_USER_AGENT is not set")
            async with httpx.AsyncClient(transport=self.transport, timeout=30, headers={"User-Agent": self.agent}) as client:
                response = await client.get(url)
                response.raise_for_status()
                body = response.text if text else response.json()
        except (httpx.HTTPError, ValueError) as error:
            if cached:
                return cached[0], f"SEC could not be reached ({type(error).__name__}); using data cached {cached[1].date().isoformat()}."
            raise SecUnavailable(f"SEC could not be reached and nothing is cached for {key}.") from error
        self.cache.put(key, body)
        return body, None

    async def company(self, position: Position, as_of: date) -> CompanyResearch:
        match = re.fullmatch(r"CIK(\d{10})", position.company_id or "")
        if not match or position.kind != "stock" or position.listing not in US_LISTINGS or position.currency != "USD":
            return await self.reviewed.company(position, as_of)
        try:
            research = await self._sec(int(match.group(1)), position.company_id or "", as_of)
        except SecUnavailable as error:
            research = CompanyResearch(company_id=position.company_id or "", sector="unknown", cyclical=None, documents=[], facts=[],
                                       issues=[f"{error} Company facts remain unknown."])
        manual = self.reviewed.records and await self.reviewed.company(position, as_of)
        return merge(research, manual) if manual and manual.documents else research

    async def _sec(self, cik: int, company_id: str, as_of: date) -> CompanyResearch:
        issues: list[str] = []
        submissions, note = await self._fetch(f"submissions/CIK{cik:010d}.json", SUBMISSIONS_URL.format(cik=cik), daily=True)
        if note:
            issues.append(note)
        recent = submissions.get("filings", {}).get("recent", {})
        filings = [dict(zip(recent, row, strict=True)) for row in zip(*recent.values(), strict=True)] if recent else []
        filings = [row for row in filings if row.get("filingDate", "9999") <= as_of.isoformat()]
        annual = next((row for row in filings if row["form"] == "10-K"), None)
        quarter = next((row for row in filings if row["form"] == "10-Q" and (annual is None or row["filingDate"] > annual["filingDate"])), None)
        eight_ks = [row for row in filings if row["form"] == "8-K" and RELEVANT_8K & set(str(row.get("items", "")).split(","))
                    and (as_of - date.fromisoformat(row["filingDate"])).days <= 180][:4]
        if annual is None:
            issues.append("No 10-K on SEC EDGAR by the decision date; annual facts remain unknown.")

        facts_body, note = await self._fetch(f"companyfacts/CIK{cik:010d}.json", FACTS_URL.format(cik=cik), daily=True)
        if note and note not in issues:
            issues.append(note)
        gaap = facts_body.get("facts", {}).get("us-gaap", {})
        facts: list[ResearchFact] = []
        documents: list[ResearchDocument] = []
        for filing, kind in ((annual, "10-K"), (quarter, "10-Q")):
            if filing is None:
                continue
            doc_id = f"sec-{kind.lower()}-{filing['accessionNumber']}"
            found = extract(gaap, filing, kind, doc_id, cik, issues)
            facts += found
            documents.append(filing_document(filing, kind, doc_id, cik, company_id, found))
        facts = split_check(facts, facts_body.get("facts", {}).get("dei", {}), as_of, issues)
        for filing in eight_ks:
            documents.append(await self._eight_k(filing, cik, company_id, issues))
        sector = sector_of(str(submissions.get("sic") or ""))
        if sector == "unknown":
            issues.append("SEC lists no industry code; the valuation sector is unknown.")
        return CompanyResearch(company_id=company_id, sector=sector, cyclical=None,  # type: ignore[arg-type]
                               documents=documents[:20], facts=facts[:40], issues=issues[:40])

    async def _eight_k(self, filing: dict[str, Any], cik: int, company_id: str, issues: list[str]) -> ResearchDocument:
        accession = filing["accessionNumber"]
        folder = f"{ARCHIVES_URL}/{cik}/{accession.replace('-', '')}"
        items = [item for item in str(filing.get("items", "")).split(",") if item]
        listed = "; ".join(f"Item {item} {ITEMS.get(item, '')}".strip() for item in items)
        common = {"company_id": company_id, "published_on": filing["filingDate"], "as_of": filing.get("reportDate") or filing["filingDate"],
                  "qa_available": False}
        if "2.02" in items:
            # The earnings release the issuer furnished as an exhibit: the issuer's own words, read as untrusted text.
            try:
                index, _ = await self._fetch(f"filings/{accession}/index.json", f"{folder}/index.json", daily=False)
                names = [row["name"] for row in index.get("directory", {}).get("item", [])]
                exhibit = next((name for name in names if re.search(r"ex-?99", name, re.I) and name.lower().endswith((".htm", ".html"))), None)
                if exhibit is None:
                    raise SecUnavailable("no exhibit 99")
                page, _ = await self._fetch(f"filings/{accession}/{exhibit}.txt", f"{folder}/{exhibit}", daily=False, text=True)
                return ResearchDocument(id=f"issuer-8k-{accession}", authority="issuer", url=f"{folder}/{exhibit}", available=True,
                                        title=f"Earnings release furnished on Form 8-K ({filing['filingDate']})", excerpt=text_of(page)[:EXCERPT], **common)
            except SecUnavailable:
                issues.append(f"8-K {accession}: the earnings-release exhibit could not be read.")
                return ResearchDocument(id=f"issuer-8k-{accession}", authority="issuer", url=f"{folder}/{filing['primaryDocument']}", available=False,
                                        title=f"Earnings release furnished on Form 8-K ({filing['filingDate']})", excerpt="", **common)
        return ResearchDocument(id=f"sec-8k-{accession}", authority="sec", url=f"{folder}/{filing['primaryDocument']}", available=True,
                                title=f"Form 8-K ({filing['filingDate']}): {listed}", excerpt=f"Form 8-K filed {filing['filingDate']} reporting {listed}.", **common)


def split_check(facts: list[ResearchFact], dei: dict[str, Any], as_of: date, issues: list[str]) -> list[ResearchFact]:
    """Diluted shares against the latest cover-page share count. A ratio that looks like a split or reverse split
    (more than 1.8x apart) means per-share figures and today's price may be on different bases, so those share
    facts are dropped and stay unknown. Companies with several share classes report no single cover count; no check."""
    rows = [row for row in dei.get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", []) if row.get("filed", "9999") <= as_of.isoformat()]
    if not rows:
        return facts
    cover = max(rows, key=lambda row: (row["filed"], row["end"]))
    count = Decimal(str(cover["val"]))
    kept = []
    for fact in facts:
        if fact.metric == "shares" and fact.value and count > 0 and not Decimal("0.55") <= fact.value / count <= Decimal("1.8"):
            issues.append(f"{fact.id}: {fact.value:,} diluted shares against {count:,} outstanding on {cover['end']} looks like a stock split "
                          "or share-count change; per-share figures for that period stay unknown.")
            continue
        kept.append(fact)
    return kept


def _days(row: dict[str, Any]) -> int | None:
    return (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days if row.get("start") else None


def _periods(gaap: dict[str, Any], accession: str, kind: str, report: str) -> list[tuple[str | None, str, str]]:
    """The reporting periods a filing's facts use: (start, end, label). A 10-K gives up to three fiscal years and its
    balance-sheet date; a 10-Q gives its quarter, its year to date and its balance-sheet date."""
    seen: set[tuple[str | None, str]] = set()
    for concepts in CONCEPTS.values():
        for concept in concepts:
            for unit in gaap.get(concept, {}).get("units", {}).values():
                seen |= {(row.get("start"), row["end"]) for row in unit if row.get("accn") == accession}
    periods: list[tuple[str | None, str, str]] = [(None, report, "at")]
    if kind == "10-K":
        years = sorted({(start, end) for start, end in seen if start and 350 <= _days({"start": start, "end": end}) <= 380 and end <= report},  # type: ignore[operator]
                       key=lambda period: period[1], reverse=True)[:3]
        periods += [(start, end, "fy") for start, end in years]
    else:
        quarter = [(start, end) for start, end in seen if start and end == report and 80 <= _days({"start": start, "end": end}) <= 100]  # type: ignore[operator]
        to_date = sorted([(start, end) for start, end in seen if start and end == report and 100 < _days({"start": start, "end": end}) < 350],  # type: ignore[operator]
                         key=lambda period: period[0] or "")
        periods += [(start, end, "q") for start, end in quarter[:1]] + [(start, end, "ytd") for start, end in to_date[:1]]
    return periods


def _values(gaap: dict[str, Any], concept: str, accession: str, start: str | None, end: str) -> list[dict[str, Any]]:
    unit_name = "shares" if concept.startswith("WeightedAverage") else "USD"
    rows = gaap.get(concept, {}).get("units", {}).get(unit_name, [])
    return [{**row, "concept": concept, "unit": unit_name} for row in rows
            if row.get("accn") == accession and row["end"] == end and row.get("start") == start]


def _source(row: dict[str, Any], cik: int) -> FactSource:
    return FactSource(taxonomy="us-gaap", concept=row["concept"], unit=row["unit"], accession=row["accn"], form=row["form"],
                      fiscal_year=row.get("fy"), fiscal_period=row.get("fp"), filed=row["filed"], period_start=row.get("start"),
                      period_end=row["end"], value=Decimal(str(row["val"])), url=FACTS_URL.format(cik=cik))


def extract(gaap: dict[str, Any], filing: dict[str, Any], kind: str, doc_id: str, cik: int, issues: list[str]) -> list[ResearchFact]:
    """Facts one filing reports, chosen per metric and period. A metric with no value stays out (unknown)."""
    accession, report = filing["accessionNumber"], filing.get("reportDate") or ""
    found: list[ResearchFact] = []

    def add(metric: str, label: str, start: str | None, end: str, value: Decimal, rows: list[dict[str, Any]], definition: str, agreed: bool) -> None:
        found.append(ResearchFact(
            id=f"{metric}-{label}-{end}", metric=metric, value=value, unit="shares" if metric == "shares" else "currency",  # type: ignore[arg-type]
            currency=None if metric == "shares" else "USD", period_start=date.fromisoformat(start) if start else None,
            period_end=date.fromisoformat(end), document_ids=[doc_id],
            definition=f"{definition}; {'+'.join(row['concept'] for row in rows)} from {kind} {accession} filed {filing['filingDate']}"
                       f"{' (year to date)' if label == 'ytd' else ''}. SEC CompanyFacts, consolidated (no segment dimensions).",
            filing_checked=True, notes_checked=False, custom_tags_checked=agreed, segments_checked=True, review="sec_xbrl",
            sources=[_source(row, cik) for row in rows]))

    for start, end, label in _periods(gaap, accession, kind, report):
        metrics = ("cash",) if label == "at" else CASH_FLOW if label == "ytd" else DURATION
        chosen: dict[str, tuple[Decimal, dict[str, Any], bool]] = {}
        for metric in metrics:
            candidates = [row for concept in CONCEPTS[metric] for row in _values(gaap, concept, accession, start, end)]
            if not candidates:
                if label in {"fy", "at"} and end == report:
                    issues.append(f"{metric} for the period ending {end}: not reported under {', '.join(CONCEPTS[metric])} in {kind} {accession}; unknown.")
                continue
            values = {Decimal(str(row["val"])) for row in candidates}
            agreed = len(values) == 1
            if not agreed:
                issues.append(f"{metric} for the period ending {end}: alternative or duplicate XBRL tags disagree ({', '.join(sorted({row['concept'] for row in candidates}))}); not used.")
            first = candidates[0]
            chosen[metric] = (Decimal(str(first["val"])), first, agreed)
            add(metric, label, start, end, chosen[metric][0], [first], DEFINITIONS[metric], agreed)
        if "operating_cash_flow" in chosen and "capex" in chosen:
            (ocf, ocf_row, ocf_ok), (capex, capex_row, capex_ok) = chosen["operating_cash_flow"], chosen["capex"]
            add("free_cash_flow", label, start, end, ocf - capex, [ocf_row, capex_row],
                "Free cash flow calculated here as operating cash flow minus capital expenditure", ocf_ok and capex_ok)
        if label == "at":
            debt = _debt(gaap, accession, end)
            if debt is None:
                issues.append(f"total_debt at {end}: no complete debt figures in {kind} {accession}; unknown.")
            else:
                add("total_debt", label, None, end, debt[0], debt[1], debt[2], True)
    return found


def _debt(gaap: dict[str, Any], accession: str, end: str) -> tuple[Decimal, list[dict[str, Any]], str] | None:
    first = lambda concept: next(iter(_values(gaap, concept, accession, None, end)), None)  # noqa: E731
    combined = first(DEBT_TOTAL)
    if combined:
        return Decimal(str(combined["val"])), [combined], "Total debt as reported (long- and short-term combined)"
    long_term = first("LongTermDebt")
    parts = [long_term] if long_term else [first("LongTermDebtNoncurrent"), first("LongTermDebtCurrent")]
    if not all(parts):
        return None
    short = next((row for row in (first(concept) for concept in DEBT_SHORT) if row), None)
    rows = [row for row in [*parts, short] if row]
    return (sum((Decimal(str(row["val"])) for row in rows), Decimal(0)), rows,
            "Total debt calculated here as long-term debt including current maturities plus short-term borrowings or commercial paper where reported")


def filing_document(filing: dict[str, Any], kind: str, doc_id: str, cik: int, company_id: str, facts: list[ResearchFact]) -> ResearchDocument:
    """The 10-K or 10-Q itself, with the structured figures it reported as its excerpt."""
    accession = filing["accessionNumber"]
    period = "fiscal year" if kind == "10-K" else "quarter"
    lines = [f"Form {kind} for the {period} ended {filing.get('reportDate')}, filed {filing['filingDate']}, accession {accession}.",
             "Figures this filing reported in XBRL (consolidated):"]
    lines += [f"- {fact.metric.replace('_', ' ')} {fact.period_start or ''}{' to ' if fact.period_start else 'at '}{fact.period_end}: "
              f"{fact.value:,} {fact.currency or fact.unit}" for fact in facts]
    return ResearchDocument(id=doc_id, authority="sec", company_id=company_id, url=f"{ARCHIVES_URL}/{cik}/{accession.replace('-', '')}/{filing['primaryDocument']}",
                            published_on=filing["filingDate"], as_of=filing.get("reportDate") or filing["filingDate"],
                            title=f"Form {kind}, {period} ended {filing.get('reportDate')}", excerpt="\n".join(lines)[:20000],
                            available=True, qa_available=False)


def merge(sec: CompanyResearch, manual: CompanyResearch) -> CompanyResearch:
    """SEC research plus the reviewed reference record as supplemental evidence. A reviewed sector or cyclicality
    fills what SEC cannot say; reviewed facts sit beside SEC's, so a disagreement surfaces as a contradiction."""
    ids = {row.id for row in sec.documents} | {row.id for row in sec.facts}
    documents = [*sec.documents, *(doc for doc in manual.documents if doc.id not in ids)][:20]
    kept = {doc.id for doc in documents}
    facts = [*sec.facts, *(fact for fact in manual.facts if fact.id not in ids and set(fact.document_ids) <= kept)][:40]
    return CompanyResearch(company_id=sec.company_id, sector=manual.sector if manual.sector != "unknown" else sec.sector,
                           cyclical=manual.cyclical if manual.cyclical is not None else sec.cyclical,
                           documents=documents, facts=facts, issues=[*sec.issues, *manual.issues][:40])
