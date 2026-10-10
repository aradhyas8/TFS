"""Issuer-published research for companies the SEC path does not cover: issuer_research -> adapter -> CompanyResearch.

One adapter so far: CIBC (CM on the TSX). CIBC's Investor Relations site publishes each quarter's Supplementary
Financial Information as an Excel workbook, which reports the bank figures Stock Analysis needs directly: revenue, net
income, diluted EPS, dividends, book value per share, ROE, shares, common shareholders' equity and CET1. The latest
workbook gives the latest quarter; the latest fiscal year-end (October) workbook gives the annual figures. Nothing is
calculated here except unit scaling, so no book value per share or free cash flow is derived.

SEDAR+ is never fetched. Each period's SEDAR+ filing is a verification link pinned by hand below; a period without
one keeps its facts but the calculator cannot use them, and the issue says so. Downloads are cached on disk: the IR
page for a day, each workbook for good.
"""

import base64
import io
import os
import re
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, NamedTuple, Protocol
from xml.etree import ElementTree

import httpx

from .research import ResearchProvider
from .schemas import (
    CANADIAN_LISTINGS,
    CompanyResearch,
    FactSource,
    Position,
    ResearchDocument,
    ResearchFact,
)
from .sec_research import SecCache

CIBC = "CIK0001045520"
CIBC_SITE = "https://www.cibc.com"
CIBC_RESULTS = CIBC_SITE + "/en/about-cibc/investor-relations/quarterly-results.html"
SFI_LINK = re.compile(r'href="(/content/dam/[^"]+/quarterly-results/\d{4}/sfi-(january|april|july|october)(\d{2})-en\.xlsx)"')
QUARTER_END = {"january": (1, 31, 1), "april": (4, 30, 2), "july": (7, 31, 3), "october": (10, 31, 4)}  # month, day, fiscal quarter
QUARTER_START = {1: (11, 1), 2: (2, 1), 3: (5, 1), 4: (8, 1)}

# SEDAR+ verification links for CIBC's filed report covering each period, keyed by period end. Pinned by hand from
# CIBC's SEDAR+ profile (never fetched): the annual report for October, the quarterly report to shareholders otherwise.
SEDAR_LINKS: dict[date, dict[str, str]] = {}


class Row(NamedTuple):
    metric: str
    sheet: str
    labels: tuple[str, ...]  # the row label as printed, footnote markers removed (CIBC's wording varies between quarters)
    kind: str  # "millions", "thousands", "per_share" or "ratio"
    point: bool  # a balance at period end rather than a flow over the period
    definition: str


ROWS = (
    Row("revenue", "Pg 4 FH", ("Total revenue",), "millions", False, "Total revenue as reported (net interest income plus non-interest income)"),
    Row("net_income", "Pg 4 FH", ("Net income",), "millions", False,
        "Reported net income, including amounts attributable to non-controlling interests and preferred shareholders"),
    Row("eps_diluted", "Pg 4 FH", ("Reported diluted EPS",), "per_share", False, "Reported diluted earnings per common share"),
    Row("dividend_per_share", "Pg 4 FH", ("Dividends",), "per_share", False, "Dividends declared per common share"),
    Row("book_value_per_share", "Pg 4 FH", ("Book value",), "per_share", True, "Book value per common share as reported"),
    Row("roe", "Pg 4 FH", ("Reported return on common shareholders' equity",), "ratio", False,
        "Reported return on common shareholders' equity (a quarter's figure is annualized by CIBC)"),
    Row("weighted_diluted_shares", "Pg 4 FH", ("Weighted-average diluted",), "thousands", False, "Weighted-average diluted common shares"),
    Row("shares", "Pg 4 FH", ("End of period",), "thousands", True, "Common shares outstanding at period end"),
    Row("book_value", "Pg 5 FH Contd", ("Common shareholders' equity",), "millions", True,
        "Common shareholders' equity at period end (excludes preferred shares, other equity instruments and non-controlling interests)"),
    Row("cet1_ratio", "Pg 5 FH Contd", ("Common Equity Tier 1 ratio", "CET1 ratio"), "ratio", True, "Common Equity Tier 1 capital ratio (OSFI basis)"),
)
SCALE = {"millions": Decimal(1_000_000), "thousands": Decimal(1000), "per_share": Decimal(1), "ratio": Decimal(1)}
SOURCE_UNIT = {"millions": "CAD millions", "thousands": "thousand shares", "per_share": "CAD per share", "ratio": "percent"}
FACT_UNIT = {"millions": ("currency", "CAD"), "thousands": ("shares", None), "per_share": ("per_share", "CAD"), "ratio": ("ratio", None)}
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


class IssuerAdapter(Protocol):
    async def company(self, position: Position, as_of: date) -> CompanyResearch: ...


class IssuerResearchProvider:
    """Issuer adapters first for the companies they cover; everything else goes to the wrapped provider unchanged."""

    def __init__(self, fallback: ResearchProvider, adapters: dict[str, IssuerAdapter]) -> None:
        self.fallback, self.adapters = fallback, adapters

    @classmethod
    def from_environment(cls, fallback: ResearchProvider) -> "IssuerResearchProvider":
        return cls(fallback, {CIBC: CibcAdapter()})

    async def company(self, position: Position, as_of: date) -> CompanyResearch:
        adapter = self.adapters.get(position.company_id or "")
        if adapter is None or position.kind != "stock" or position.listing not in CANADIAN_LISTINGS:
            return await self.fallback.company(position, as_of)
        return await adapter.company(position, as_of)


def workbook(content: bytes) -> dict[str, dict[str, str]]:
    """Every sheet of an .xlsx as {cell reference: text}, read with the standard library."""
    archive = zipfile.ZipFile(io.BytesIO(content))
    names = archive.namelist()
    shared = []
    if "xl/sharedStrings.xml" in names:
        shared = ["".join(text.text or "" for text in item.iter(f"{{{NS['m']}}}t"))
                  for item in ElementTree.fromstring(archive.read("xl/sharedStrings.xml")).findall("m:si", NS)]
    targets = {rel.get("Id"): rel.get("Target", "") for rel in ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
    sheets: dict[str, dict[str, str]] = {}
    for sheet in ElementTree.fromstring(archive.read("xl/workbook.xml")).iter(f"{{{NS['m']}}}sheet"):
        target = targets[sheet.get(REL)].lstrip("/")
        cells: dict[str, str] = {}
        for cell in ElementTree.fromstring(archive.read(target if target.startswith("xl/") else f"xl/{target}")).iter(f"{{{NS['m']}}}c"):
            value = cell.find("m:v", NS)
            text = (shared[int(value.text)] if cell.get("t") == "s" else value.text) if value is not None and value.text else None
            inline = cell.find("m:is", NS)
            if text is None and inline is not None:
                text = "".join(part.text or "" for part in inline.iter(f"{{{NS['m']}}}t"))
            if text and text.strip():
                cells[cell.get("r", "")] = text.strip()
        sheets[sheet.get("name", "")] = cells
    return sheets


def _split(ref: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", ref)
    assert match
    return match.group(1), int(match.group(2))


def label_of(text: str) -> str:
    return re.sub(r"(?:\s*\(\d+\))+\s*$", "", re.sub(r"\(\d+$", "", text)).strip()  # "Book value (1)" -> "Book value"


def number(text: str, kind: str) -> Decimal | None:
    """A cell as a Decimal: "(2.5)%" is -0.025, "13.4%" is 0.134; per-share amounts to the cent as printed."""
    clean = text.replace(",", "").replace("$", "").strip()
    negative = clean.startswith("(") and ")" in clean
    percent = clean.endswith("%")
    clean = clean.replace("(", "").replace(")", "").rstrip("%").strip()
    try:
        value = Decimal(clean)
    except InvalidOperation:
        return None
    value = -value if negative else value
    if kind == "ratio":
        # Printed as text ("13.4%") in some quarters, stored as a percent-formatted fraction (0.134) in others.
        return (value / 100 if percent else value).quantize(Decimal("0.0001"))
    return value.quantize(Decimal("0.01")) if kind == "per_share" else value


class Column(NamedTuple):
    letter: str
    start: date
    end: date
    label: str  # "q" or "fy"
    fiscal_year: int
    fiscal_period: str


def columns(cells: dict[str, str], period_end: date, quarter: int, fiscal_year: int) -> dict[str, Column]:
    """The quarter column (e.g. "Q3/26") and the full-year column ("12M" under the fiscal year) of one sheet."""
    found: dict[str, Column] = {}
    month, day = QUARTER_START[quarter]
    q_start = date(fiscal_year - 1 if quarter == 1 else fiscal_year, month, day)
    for ref, text in cells.items():
        letter, row = _split(ref)
        if text == f"Q{quarter}/{fiscal_year % 100:02d}" and "q" not in found:
            found["q"] = Column(letter, q_start, period_end, "q", fiscal_year, f"Q{quarter}")
        if text == "12M":
            year = cells.get(f"{letter}{row - 1}", "")
            if year.isdigit():
                year_end = date(int(year), 10, 31)
                found.setdefault(f"fy{year}", Column(letter, date(int(year) - 1, 11, 1), year_end, "fy", int(year), "FY"))
    return found


class Sfi(NamedTuple):
    name: str
    url: str
    period_end: date
    quarter: int
    fiscal_year: int
    published: date
    sheets: dict[str, dict[str, str]]


class CibcAdapter:
    def __init__(self, *, cache: SecCache | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        directory = os.environ.get("ISSUER_CACHE_DIR") or Path(__file__).resolve().parents[1] / "data" / "issuers"
        self.cache = cache or SecCache(directory)
        self.transport = transport

    async def _get(self, key: str, url: str, *, daily: bool) -> tuple[dict[str, Any], str | None]:
        """A cached download: {"text"} for a page, {"base64", "last_modified"} for a file. Daily entries are fetched
        again once a day; on an outage the cached copy is used and the note says so."""
        cached = self.cache.get(key)
        if cached and (not daily or cached[1].date() == datetime.now(UTC).date()):
            return cached[0], None
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=60, follow_redirects=True,
                                         headers={"User-Agent": "Mozilla/5.0 (compatible; personal-investment-analyst)"}) as client:
                response = await client.get(url)
                response.raise_for_status()
        except httpx.HTTPError as error:
            if cached:
                return cached[0], f"CIBC Investor Relations could not be reached ({type(error).__name__}); using a copy cached {cached[1].date()}."
            raise
        body: dict[str, Any] = ({"text": response.text} if daily else
                                {"base64": base64.b64encode(response.content).decode(), "last_modified": response.headers.get("last-modified"),
                                 "fetched": datetime.now(UTC).date().isoformat()})
        self.cache.put(key, body)
        return body, None

    async def _sfi(self, path: str, month: str, yy: str, issues: list[str]) -> Sfi | None:
        name = path.rsplit("/", 1)[1]
        body, _ = await self._get(f"cibc/{name}.json", CIBC_SITE + path, daily=False)
        month_end, day, quarter = QUARTER_END[month]
        period_end = date(2000 + int(yy), month_end, day)
        try:
            sheets = workbook(base64.b64decode(body["base64"]))
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
            issues.append(f"{name}: the workbook could not be read; its figures remain unknown.")
            return None
        cover = " ".join(sheets.get("COV", {}).values())
        if f"{period_end:%B} {period_end.day}, {period_end.year}" not in cover:
            issues.append(f"{name}: the cover does not state the period ended {period_end}; its figures remain unknown.")
            return None
        modified = body.get("last_modified")
        # The server's Last-Modified date stands for publication; without it, the day it was first downloaded.
        published = parsedate_to_datetime(modified).date() if modified else date.fromisoformat(body["fetched"])
        fiscal_year = period_end.year + (1 if period_end.month > 10 else 0)
        return Sfi(name, CIBC_SITE + path, period_end, quarter, fiscal_year, published, sheets)

    async def company(self, position: Position, as_of: date) -> CompanyResearch:
        issues: list[str] = []
        try:
            page, note = await self._get("cibc/quarterly-results.json", CIBC_RESULTS, daily=True)
            if note:
                issues.append(note)
            links = sorted({(match.group(1), match.group(2), match.group(3)) for match in SFI_LINK.finditer(page["text"])},
                           key=lambda link: (int(link[2]), QUARTER_END[link[1]][0]), reverse=True)
            candidates = [link for link in links if date(2000 + int(link[2]), *QUARTER_END[link[1]][:2]) <= as_of]
            latest = annual = None
            for path, month, yy in candidates:  # the latest workbook published by the decision date, then the latest year-end one
                if latest is not None and month != "october":
                    continue
                sfi = await self._sfi(path, month, yy, issues)
                if sfi is None or sfi.published > as_of:
                    continue
                latest = latest or sfi
                if month == "october":
                    annual = sfi
                    break
        except httpx.HTTPError as error:
            return CompanyResearch(company_id=position.company_id or CIBC, sector="financial", cyclical=None, documents=[], facts=[],
                                   issues=[f"CIBC Investor Relations could not be reached ({type(error).__name__}) and nothing is cached; "
                                           "company facts remain unknown."])
        documents: list[ResearchDocument] = []
        facts: list[ResearchFact] = []
        if latest is None:
            issues.append("No CIBC Supplementary Financial Information was published by the decision date; company facts remain unknown.")
        else:
            quarter = extract(latest, "q", issues)
            yearly = extract(annual, f"fy{annual.fiscal_year}", issues) if annual else {}
            if annual is None:
                issues.append("No fiscal year-end Supplementary Financial Information by the decision date; annual figures remain unknown.")
            elif annual is not latest:
                # The latest workbook repeats the last full year; a different figure there is a restatement, so it stays unknown.
                again = extract(latest, f"fy{annual.fiscal_year}", [])
                for metric, (value, *_rest) in list(yearly.items()):
                    if metric in again and again[metric][0] != value:
                        issues.append(f"{metric} for fiscal {annual.fiscal_year}: {annual.name} reports {value} but {latest.name} reports "
                                      f"{again[metric][0]}; not used.")
                        del yearly[metric]
            for found in (quarter, yearly):
                consistent(found, issues)
            groups: dict[str, tuple[Sfi, list[ResearchFact], ResearchDocument | None]] = {}
            for sfi, found in ((latest, quarter), (annual, yearly)):
                if sfi is None:
                    continue
                doc_id = f"issuer-cibc-sfi-{sfi.period_end}"
                sedar = groups[sfi.name][2] if sfi.name in groups else sedar_document(sfi.period_end, issues)
                made = [fact(metric, entry, sfi, [doc_id, *([sedar.id] if sedar else [])]) for metric, entry in found.items()]
                # A fiscal year-end workbook gives both its quarter and its year; balances at the same date appear once.
                known = {row.id for row in groups.get(sfi.name, (sfi, [], None))[1]}
                groups[sfi.name] = (sfi, [*groups.get(sfi.name, (sfi, [], None))[1], *(row for row in made if row.id not in known)], sedar)
            for sfi, made, sedar in groups.values():
                facts += made
                documents.append(sfi_document(sfi, f"issuer-cibc-sfi-{sfi.period_end}", made))
                if sedar:
                    documents.append(sedar)
        return CompanyResearch(company_id=position.company_id or CIBC, sector="financial", cyclical=None,
                               documents=documents[:20], facts=facts[:40], issues=list(dict.fromkeys(issues))[:40])


Entry = tuple[Decimal, Row, Column, str, str]  # value, row, column, cell, value as printed


def extract(sfi: Sfi, column: str, issues: list[str]) -> dict[str, Entry]:
    """The listed rows from one column ("q" or "fy2025"). A label found on no row, or on more than one, stays unknown."""
    found: dict[str, Entry] = {}
    for row in ROWS:
        cells = sfi.sheets.get(row.sheet, {})
        wanted = columns(cells, sfi.period_end, sfi.quarter, sfi.fiscal_year).get(column)
        period = "the quarter" if column == "q" else f"fiscal {column[2:]}"
        if wanted is None:
            issues.append(f"{row.metric} for {period}: {sfi.name} {row.sheet} has no such column; unknown.")
            continue
        rows = sorted({_split(ref)[1] for ref, text in cells.items() if _split(ref)[0] in {"A", "B", "C"} and label_of(text) in row.labels})
        if len(rows) != 1:
            issues.append(f"{row.metric} for {period}: '{row.labels[0]}' appears on {len(rows)} rows of {sfi.name} {row.sheet}; unknown.")
            continue
        ref = f"{wanted.letter}{rows[0]}"
        printed = cells.get(ref)
        value = number(printed, row.kind) if printed else None
        if value is None:
            issues.append(f"{row.metric} for {period}: {sfi.name} {row.sheet} {ref} is '{printed or 'blank'}'; unknown.")
            continue
        found[row.metric] = (value, row, wanted, ref, printed or "")
    return found


def consistent(found: dict[str, Entry], issues: list[str]) -> None:
    """The calculator values a bank at common equity over period-end shares. That must reproduce CIBC's own book value
    per share to the cent, or the two definitions differ and common equity is not used."""
    if not {"book_value", "shares", "book_value_per_share"} <= set(found):
        return
    equity, shares, reported = (found[key][0] * SCALE[found[key][1].kind] for key in ("book_value", "shares", "book_value_per_share"))
    derived = (equity / shares).quantize(Decimal("0.01"))
    if derived != reported:
        issues.append(f"Common equity / period-end shares at {found['book_value'][2].end} is {derived}, not the reported book value per share "
                      f"{reported}; common equity is not used.")
        del found["book_value"]


def fact(metric: str, entry: Entry, sfi: Sfi, document_ids: list[str]) -> ResearchFact:
    value, row, column, ref, printed = entry
    unit, currency = FACT_UNIT[row.kind]
    start = None if row.point else column.start
    label = "at" if row.point else column.label
    return ResearchFact(
        id=f"{metric}-{label}-{column.end}", metric=metric, value=value * SCALE[row.kind], unit=unit, currency=currency,  # type: ignore[arg-type]
        period_start=start, period_end=column.end, document_ids=document_ids,
        definition=f"{row.definition}; {row.sheet} {ref} ('{label_of(entry_label(sfi, row, ref))}') of CIBC Supplementary Financial Information {sfi.name}, "
                   f"{'fiscal ' + str(column.fiscal_year) if column.label == 'fy' else f'{column.fiscal_period} fiscal {column.fiscal_year}'}, "
                   f"dated {sfi.published} on CIBC's server. Reported (not adjusted) figure, consolidated.",
        filing_checked=True, notes_checked=False, custom_tags_checked=True, segments_checked=True, review="issuer_report",
        sources=[FactSource(taxonomy="cibc-sfi", concept=f"{row.sheet}!{ref} {label_of(entry_label(sfi, row, ref))}"[:120], unit=SOURCE_UNIT[row.kind], accession=sfi.name,
                            form="Supplementary Financial Information", fiscal_year=column.fiscal_year, fiscal_period=column.fiscal_period,
                            filed=sfi.published, period_start=start, period_end=column.end, value=number(printed, row.kind) or value, url=sfi.url)])


def entry_label(sfi: Sfi, row: Row, ref: str) -> str:
    """The label printed on the row a value came from."""
    number_row = _split(ref)[1]
    return next((text for letter in "ABC" if label_of(text := sfi.sheets[row.sheet].get(f"{letter}{number_row}", "")) in row.labels), row.labels[0])


def sfi_document(sfi: Sfi, doc_id: str, facts: list[ResearchFact]) -> ResearchDocument:
    lines = [f"CIBC Supplementary Financial Information for the period ended {sfi.period_end} (Q{sfi.quarter} fiscal {sfi.fiscal_year}), "
             f"on CIBC Investor Relations (server date {sfi.published}). Reported figures read from the workbook (consolidated, CAD):"]
    lines += [f"- {fact.metric.replace('_', ' ')} {fact.period_start or ''}{' to ' if fact.period_start else 'at '}{fact.period_end}: "
              f"{fact.value:,} {fact.currency or ''} {'' if fact.unit == 'currency' else fact.unit}".rstrip() for fact in facts]
    return ResearchDocument(id=doc_id, authority="issuer", company_id=CIBC, url=sfi.url, published_on=sfi.published, as_of=sfi.period_end,
                            title=f"CIBC Supplementary Financial Information, Q{sfi.quarter} fiscal {sfi.fiscal_year}",
                            excerpt="\n".join(lines)[:20000], available=True, qa_available=False)


def sedar_document(period_end: date, issues: list[str]) -> ResearchDocument | None:
    link = SEDAR_LINKS.get(period_end)
    if link is None:
        issues.append(f"SEDAR+ verification link not pinned for CIBC's filing for the period ended {period_end}; the figures rest on "
                      "CIBC's own published workbook (optional link: SEDAR_LINKS in analyst/issuer_research.py).")
        return None
    return ResearchDocument(id=f"sedar-cibc-{period_end}", authority="sedar_plus", company_id=CIBC, url=link["url"],
                            published_on=date.fromisoformat(link["filed"]), as_of=period_end, title=link["title"],
                            excerpt=f"{link['title']}, filed on SEDAR+ {link['filed']}. Open the link to verify the filed report; the "
                                    "figures were read from CIBC's Supplementary Financial Information for the same period.",
                            available=True, qa_available=False)
