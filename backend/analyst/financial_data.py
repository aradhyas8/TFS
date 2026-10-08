"""Dated financial-source boundary. No model supplies identities, prices or rates."""

import asyncio
import json
import os
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

from .schemas import (
    FX,
    US_LISTINGS,
    FinancialEvidence,
    Identity,
    Position,
    Quote,
    Snapshot,
    SourceQualification,
    SponsorHoldings,
)

# A provider price or rate dated more than this many days before the snapshot is not used.
# Covers weekends, holidays and the Bank of Canada's once-daily publication; values keep their own date.
RECENT_DAYS = 7
EODHD_URL = "https://eodhd.com/api"
EODHD_SOURCE = "EODHD"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_LISTINGS = {"Nasdaq": "XNAS", "NYSE": "XNYS"}
# EODHD exchange codes. All US listings share "US"; SEC supplies the exact US exchange.
EODHD_LISTINGS = {"TO": "XTSE", "V": "XTSX", "NEO": "NEOE", "CN": "XCNQ"}
EODHD_CODE = {**{listing: code for code, listing in EODHD_LISTINGS.items()}, "XNAS": "US", "XNYS": "US", "XASE": "US"}
SUFFIX = {"XTSE": ".TO", "XTSX": ".V", "NEOE": ".NE", "XCNQ": ".CN"}
# Chosen by the user on 2026-10-07: EODHD free plan, personal use, 20 requests a day.
EODHD_QUALIFICATION = SourceQualification(
    source=EODHD_SOURCE, terms_url="https://eodhd.com/financial-apis/terms-conditions",
    checked_on=date(2026, 10, 7), personal_use_permitted=True, covered_listings=sorted(EODHD_CODE))
# Chosen by the user on 2026-10-08 after a live acceptance test: Yahoo Finance through the unofficial
# yfinance library is the zero-cost primary quote source; EODHD remains the fallback. Not exchange data.
YAHOO_SOURCE = "Yahoo Finance via yfinance (unofficial, not exchange data)"
YAHOO_QUALIFICATION = SourceQualification(
    source=YAHOO_SOURCE, terms_url="https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html",
    checked_on=date(2026, 10, 8), personal_use_permitted=True, covered_listings=sorted(EODHD_CODE))
MARKET_TIME = ZoneInfo("America/New_York")
PRICE_PLACES = Decimal("0.0000000001")
# Yahoo symbol -> {"price", "currency", "time" (epoch seconds), "history": [(ISO date, close)]}; absent when not returned.
YahooFetch = Callable[[list[str]], dict[str, dict[str, Any]]]


class FinancialProvider(Protocol):
    async def identity(self, position: Position, as_of: date) -> Identity: ...
    async def quote(self, position: Position, as_of: date) -> Quote | None: ...
    async def fx(self, from_currency: str, to_currency: str, as_of: date) -> FX | None: ...
    async def sponsor_holdings(self, position: Position, as_of: date) -> SponsorHoldings | None: ...


class QuoteUnavailable(ValueError):
    """No usable price for this holding; the message says why."""


class SourceLimit(ValueError):
    """The market-data plan refused the request: daily limit used up or symbol not covered."""


NOT_CACHED = "No price in the market-data cache yet; use Refresh prices."


def legal_name(name: str) -> str:
    """Comparable issuer name: SEC's /STATE/ suffix, case and punctuation removed."""
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"/[A-Za-z]+/?", "", name).lower()).strip()


def eodhd_symbol(position: Position) -> str | None:
    code = EODHD_CODE.get(position.listing or "")
    return f"{position.ticker}.{code}" if code and position.ticker and position.kind != "cash" else None


def yahoo_symbol(position: Position) -> str | None:
    """US tickers as-is, Canadian ones with their exchange suffix (CM -> CM.TO); share classes use a dash."""
    if eodhd_symbol(position) is None or position.ticker is None:
        return None
    return position.ticker.replace(".", "-") + SUFFIX.get(position.listing or "", "")


def yahoo_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """One Yahoo chart request per symbol, run in parallel: latest price, currency, quote time and a month of daily closes.

    yfinance has no multi-symbol quote call; yf.download is this same per-symbol fan-out but drops currency and time.
    A symbol that errors or returns nothing is left out, so the caller can fall back for it.
    """
    import yfinance as yf  # type: ignore[import-untyped]

    def one(symbol: str) -> dict[str, Any]:
        ticker = yf.Ticker(symbol)
        closes = ticker.history(period="1mo", interval="1d", auto_adjust=False)["Close"].dropna()
        meta = ticker.get_history_metadata()
        stamp = meta.get("regularMarketTime")  # a pandas Timestamp in yfinance 1.x
        return {"price": meta.get("regularMarketPrice"), "currency": meta.get("currency"),
                "time": int(stamp.timestamp()) if hasattr(stamp, "timestamp") else stamp,
                "history": [(day.date().isoformat(), float(close)) for day, close in closes.items()]}

    found: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for symbol, future in [(symbol, pool.submit(one, symbol)) for symbol in symbols]:
            try:
                row = future.result()
            except Exception:  # noqa: BLE001 - any yfinance failure means "no quote", and the caller falls back
                continue
            if row["price"] is not None or row["history"]:
                found[symbol] = row
    return found


class QuoteCache:
    """The last fetched quote per symbol (EODHD-style key: AVGO.US, CM.TO), in one local JSON file. Analysis reads only this.

    Daily closes from the same refresh sit beside it in history.json; nothing reads them yet.
    """

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory or os.environ.get("MARKET_DATA_DIR") or Path(__file__).resolve().parents[1] / "data" / "market")
        self.path = self.directory / "quotes.json"
        self.history_path = self.directory / "history.json"

    def get(self) -> dict[str, Quote]:
        if not self.path.exists():
            return {}
        return {symbol: Quote.model_validate(row) for symbol, row in json.loads(self.path.read_text(encoding="utf-8")).items()}

    def put(self, quotes: dict[str, Quote]) -> None:
        merged = {**self.get(), **quotes}
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({symbol: row.model_dump(mode="json") for symbol, row in merged.items()}, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def get_history(self) -> dict[str, Any]:
        return json.loads(self.history_path.read_text(encoding="utf-8")) if self.history_path.exists() else {}

    def put_history(self, history: dict[str, Any]) -> None:
        merged = {**self.get_history(), **history}
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.history_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        temporary.replace(self.history_path)


class PersonalFinancialProvider:
    """Reviewed reference file first, then SEC and EODHD identity, cached quotes and Valet FX.

    EODHD is never primary-source verification: only an SEC registrant match verifies an issuer.
    Quotes come only from the local cache, which refresh_quotes fills at most once per symbol per day
    unless the user forces a refresh: yfinance first, EODHD for whatever yfinance misses. yfinance is
    used for quotes and daily closes only, never identity, FX, filings or holdings.
    """

    def __init__(self, reference: FinancialEvidence | None = None, *, valet: bool = False,
                 transport: httpx.AsyncBaseTransport | None = None, eodhd_key: str = "", sec_agent: str = "",
                 cache: QuoteCache | None = None, yahoo: YahooFetch | None = None) -> None:
        self.reference = reference or FinancialEvidence()
        self.valet = valet
        self.transport = transport
        self.eodhd_key = eodhd_key
        self.sec_agent = sec_agent
        self.cache = cache
        self.yahoo = yahoo
        self._sec: tuple[dict[str, tuple[int, str, str]], dict[str, int]] | None = None

    @classmethod
    def from_environment(cls) -> "PersonalFinancialProvider":
        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
        path = os.environ.get("FINANCIAL_REFERENCE_FILE")
        reference = FinancialEvidence.model_validate_json(Path(path).read_text(encoding="utf-8")) if path else None
        holdings_path = os.environ.get("SPONSOR_HOLDINGS_REFERENCE_FILE")
        if holdings_path:
            extra = json.loads(Path(holdings_path).read_text(encoding="utf-8"))
            if reference is None:
                reference = FinancialEvidence()
            for key, val in extra.items():
                reference.sponsor_holdings[key] = SponsorHoldings.model_validate(val) if val else None
        return cls(reference, valet=os.environ.get("BOC_FX_ENABLED", "true").lower() == "true",
                   eodhd_key=os.environ.get("EODHD_API_KEY", ""), sec_agent=os.environ.get("SEC_USER_AGENT", ""),
                   cache=QuoteCache(), yahoo=yahoo_quotes if os.environ.get("YFINANCE_ENABLED", "true").lower() == "true" else None)

    async def _get(self, url: str, params: dict[str, str], headers: dict[str, str] | None = None) -> Any:
        async with httpx.AsyncClient(transport=self.transport, timeout=20, headers=headers) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def _eodhd(self, path: str, **params: str) -> Any:
        # The key travels only in the request; never put it in a source URL, issue or log.
        try:
            return await self._get(f"{EODHD_URL}/{path}", {**params, "api_token": self.eodhd_key, "fmt": "json"})
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {402, 403, 429}:
                raise SourceLimit from None
            raise

    async def remaining_calls(self) -> int | None:
        """Requests left today on the EODHD plan. The /user call itself is not counted by EODHD."""
        try:
            user = await self._eodhd("user")
            used = int(user["apiRequests"]) if user.get("apiRequestsDate") == datetime.now(UTC).date().isoformat() else 0
            return max(int(user["dailyRateLimit"]) - used, 0)
        except (*SOURCE_ERRORS, SourceLimit, AttributeError):
            return None

    async def _sec_index(self) -> tuple[dict[str, tuple[int, str, str]], dict[str, int]]:
        """SEC registrants by US ticker and by unique legal name. Free; costs no EODHD calls."""
        if self._sec is None:
            payload = await self._get(SEC_TICKERS_URL, {}, {"User-Agent": self.sec_agent}) if self.sec_agent else {}
            fields = payload.get("fields", []) if isinstance(payload, dict) else []
            tickers: dict[str, tuple[int, str, str]] = {}
            names: dict[str, set[int]] = {}
            if fields[:4] == ["cik", "name", "ticker", "exchange"]:
                for cik, name, ticker, exchange in (row[:4] for row in payload.get("data", [])):
                    names.setdefault(legal_name(str(name)), set()).add(int(cik))
                    if exchange in SEC_LISTINGS and ticker not in tickers:
                        tickers[ticker] = (int(cik), str(name), SEC_LISTINGS[exchange])
            self._sec = tickers, {name: next(iter(ciks)) for name, ciks in names.items() if len(ciks) == 1}
        return self._sec

    async def _sec_safe(self) -> tuple[dict[str, tuple[int, str, str]], dict[str, int]]:
        try:
            return await self._sec_index()
        except SOURCE_ERRORS:
            self._sec = None
            return {}, {}  # SEC unavailable: identities stay supplied, never verified

    async def lookup(self, ticker: str, listing: str | None, currency: str | None, as_of: date) -> list[Identity]:
        """Every listing matching what the user gave. More than one means ask; none leaves it unknown.

        SEC answers US stocks for free. EODHD search (one call) is used only for Canadian listings
        and US securities SEC does not list, such as ETFs.
        """
        tickers, names = await self._sec_safe()
        now = datetime.now(UTC)
        want_us = listing in US_LISTINGS or listing is None and currency in {None, "USD"}
        want_ca = listing in EODHD_LISTINGS.values() or listing is None and currency in {None, "CAD"}
        found: list[Identity] = []
        sec = tickers.get(ticker) if want_us else None
        if sec and listing in {None, sec[2]}:
            found.append(Identity(status="verified", ticker=ticker, listing=sec[2], currency="USD", kind="stock",
                                  company_id=f"CIK{sec[0]:010d}", company_name=sec[1], source=f"SEC registrant CIK{sec[0]:010d}",
                                  source_url=SEC_TICKERS_URL, as_of=as_of, captured_at=now))
        exchange = EODHD_CODE.get(listing or "") or ("TO" if want_ca else "US")
        if not self.eodhd_key or not (want_ca or want_us and not found):
            return found
        try:
            rows = await self._eodhd(f"search/{ticker}", exchange=exchange, limit="10")
        except (*SOURCE_ERRORS, SourceLimit):
            return found
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or row.get("Code") != ticker or row.get("Exchange") != exchange:
                continue
            quoted = str(row.get("Currency") or "")
            if len(quoted) != 3 or currency and quoted != currency:
                continue
            kinds: dict[str, Literal["stock", "etf"]] = {"etf": "etf", "common stock": "stock", "preferred stock": "stock"}
            kind = kinds.get(str(row.get("Type") or "").lower())
            name = str(row.get("Name") or ticker)
            cik = names.get(legal_name(name)) if kind == "stock" and exchange != "US" else None
            identity = Identity(status="supplied", ticker=ticker, listing=EODHD_LISTINGS.get(exchange), currency=quoted,
                                kind=kind, company_name=name, source=f"{EODHD_SOURCE} search {ticker}.{exchange}",
                                source_url=f"{EODHD_URL}/search/{ticker}?exchange={exchange}", as_of=as_of, captured_at=now,
                                company_id=None if kind != "stock" else f"CIK{cik:010d}" if cik else f"ISIN-{row['ISIN']}" if row.get("ISIN") else None)
            if cik is not None:
                identity = identity.model_copy(update={"status": "verified", "source": f"SEC registrant CIK{cik:010d}; {identity.source}"})
            found.append(identity)
        return found

    async def identity(self, position: Position, as_of: date) -> Identity:
        """The identity resolved at import, re-checked against SEC only. Costs no EODHD calls."""
        reference = self.reference.identities.get(position.id)
        if reference is not None:
            return reference
        identity = Identity(
            status="supplied", ticker=position.ticker, listing=position.listing,
            currency=position.currency, kind=position.kind if position.kind != "cash" else None,
            company_id=position.company_id, company_name=position.company_name,
            source="Imported listing identity" if position.company_name else "User-supplied listing identity", as_of=as_of,
        )
        if position.kind == "stock" and position.ticker and position.listing and position.company_name and position.company_id:
            tickers, names = await self._sec_safe()
            sec = tickers.get(position.ticker)
            cik = (sec[0] if sec and sec[2] == position.listing else None) if position.listing in US_LISTINGS else names.get(legal_name(position.company_name))
            if cik is not None and position.company_id == f"CIK{cik:010d}":
                identity = identity.model_copy(update={"status": "verified", "source": f"SEC registrant CIK{cik:010d}",
                                                       "source_url": SEC_TICKERS_URL, "captured_at": datetime.now(UTC)})
        return identity

    async def quote(self, position: Position, as_of: date) -> Quote | None:
        """The cached quote only. Analysis never calls the market-data source."""
        supplied = self.reference.quotes.get(position.id)
        symbol = eodhd_symbol(position)
        if supplied or self.cache is None or symbol is None:
            return supplied
        cached = self.cache.get().get(symbol)
        if cached is None:
            raise QuoteUnavailable(NOT_CACHED)
        if cached.captured_at and cached.captured_at.astimezone(MARKET_TIME).date() < datetime.now(MARKET_TIME).date():
            cached = cached.model_copy(update={"status": "cached"})  # fetched on an earlier day: not today's delayed quote
        return cached

    async def refresh_quotes(self, positions: list[Position], *, force: bool = False) -> dict[str, Any]:
        """Fetches each holding's delayed quote at most once per market day: yfinance first, then EODHD within its daily limit."""
        if self.cache is None:
            return {"fetched": [], "fresh": [], "skipped": [], "failed": [], "remaining": None, "message": "No market-data cache."}
        today = datetime.now(MARKET_TIME).date()
        cached = self.cache.get()
        wanted = {symbol: position for position in positions if (symbol := eodhd_symbol(position))}
        fresh = [s for s, row in cached.items() if s in wanted and not force and row.captured_at
                 and row.captured_at.astimezone(MARKET_TIME).date() == today]
        due = [symbol for symbol in wanted if symbol not in fresh]
        result: dict[str, Any] = {"fetched": [], "fresh": fresh, "skipped": [], "failed": [], "remaining": None, "message": ""}
        if not due:
            result["message"] = "Prices already fetched today; no provider calls used."
            return result
        yahoo = await self._yahoo_refresh({symbol: wanted[symbol] for symbol in due}) if self.yahoo else {}
        result["fetched"] = sorted(yahoo)
        rest = [symbol for symbol in due if symbol not in yahoo]
        if not rest:
            result["message"] = "Delayed prices fetched from Yahoo Finance (yfinance)."
            return result
        await self._eodhd_refresh(rest, wanted, result)
        if yahoo:
            result["message"] = f"{len(yahoo)} priced by Yahoo Finance (yfinance); EODHD fallback for {len(rest)}: {result['message']}"
        return result

    async def _yahoo_refresh(self, due: dict[str, Position]) -> dict[str, Quote]:
        """Caches the yfinance quotes it can validate. A failed call, wrong currency or bad price leaves that symbol for EODHD."""
        assert self.cache is not None and self.yahoo is not None
        symbols = {yahoo: symbol for symbol, position in due.items() if (yahoo := yahoo_symbol(position))}
        try:
            rows = await asyncio.to_thread(self.yahoo, list(symbols))
        except Exception:  # noqa: BLE001 - yfinance is unofficial; any failure means fall back
            return {}
        now = datetime.now(UTC)
        quotes: dict[str, Quote] = {}
        history: dict[str, Any] = {}
        for yahoo, symbol in symbols.items():
            row, position = rows.get(yahoo), due[symbol]
            try:
                assert row is not None and row.get("currency") == position.currency
                price = Decimal(str(row["price"])).quantize(PRICE_PLACES)
                traded = datetime.fromtimestamp(int(row["time"]), MARKET_TIME).date()
                assert price.is_finite() and price > 0
                closes = {day: str(Decimal(str(close)).quantize(PRICE_PLACES)) for day, close in row.get("history") or []}
            except (AssertionError, KeyError, TypeError, ValueError, ArithmeticError):
                continue
            quotes[symbol] = Quote(value=price, as_of=traded, source=YAHOO_SOURCE, captured_at=now, basis="unadjusted",
                                   ticker=position.ticker or "", listing=position.listing or "", currency=position.currency,
                                   status="delayed", qualification=YAHOO_QUALIFICATION)
            history[symbol] = {"source": YAHOO_SOURCE, "symbol": yahoo, "currency": position.currency, "basis": "unadjusted",
                               "captured_at": now.isoformat(), "daily_close": closes}
        self.cache.put(quotes)
        self.cache.put_history(history)
        return quotes

    async def _eodhd_refresh(self, due: list[str], wanted: dict[str, Position], result: dict[str, Any]) -> None:
        """EODHD real-time for the symbols yfinance missed, within the plan's daily limit. Fills result in place."""
        assert self.cache is not None
        if not self.eodhd_key:
            result.update(skipped=due, message="No EODHD key configured; prices stay unknown.")
            return
        remaining = await self.remaining_calls()
        batch = due if remaining is None else due[:remaining]
        result["skipped"] = due[len(batch):]
        if batch:
            try:
                rows = await self._eodhd(f"real-time/{batch[0]}", **({"s": ",".join(batch[1:])} if batch[1:] else {}))
            except SourceLimit:
                result.update(skipped=due, message="EODHD daily request limit reached; cached prices are kept and new prices wait until tomorrow.")
                return
            except SOURCE_ERRORS:
                result.update(failed=batch, message="EODHD could not be reached; cached prices are kept.")
                return
            now = datetime.now(UTC)
            quotes: dict[str, Quote] = {}
            for row in rows if isinstance(rows, list) else [rows]:
                symbol = str(row.get("code")) if isinstance(row, dict) else ""
                position = wanted.get(symbol)
                try:
                    price = Decimal(str(row["close"])).quantize(PRICE_PLACES)
                    traded = datetime.fromtimestamp(int(row["timestamp"]), MARKET_TIME).date()
                except (KeyError, TypeError, ValueError, ArithmeticError):
                    continue
                if position is None or not price.is_finite() or price <= 0:
                    continue
                # EODHD real-time is delayed (about 15-20 minutes); its quote has no currency, the listing's applies.
                quotes[symbol] = Quote(value=price, as_of=traded, source=EODHD_SOURCE, captured_at=now, basis="unadjusted",
                                       ticker=position.ticker or "", listing=position.listing or "", currency=position.currency,
                                       status="delayed", qualification=EODHD_QUALIFICATION)
            self.cache.put(quotes)
            result["fetched"] = sorted([*result["fetched"], *quotes])
            result["failed"] = [symbol for symbol in batch if symbol not in quotes]
        result["remaining"] = None if remaining is None else remaining - len(batch)
        result["message"] = (f"{len(result['skipped'])} holdings wait for tomorrow: EODHD daily limit reached." if result["skipped"]
                             else "Delayed prices fetched." if result["fetched"] else "No prices returned.")

    async def sponsor_holdings(self, position: Position, as_of: date) -> SponsorHoldings | None:
        holdings = self.reference.sponsor_holdings.get(position.id)
        if holdings is not None:
            return holdings
        if position.ticker and position.listing:
            holdings = self.reference.sponsor_holdings.get(f"{position.ticker}:{position.listing}")
            if holdings is not None:
                return holdings
        if position.ticker:
            holdings = self.reference.sponsor_holdings.get(position.ticker)
            if holdings is not None:
                return holdings
        return None

    async def fx(self, from_currency: str, to_currency: str, as_of: date) -> FX | None:
        supplied = next((rate for rate in self.reference.fx if
                         (rate.from_currency, rate.to_currency) == (from_currency, to_currency)), None)
        if supplied or not self.valet or "CAD" not in {from_currency, to_currency}:
            return supplied
        foreign = from_currency if to_currency == "CAD" else to_currency
        series = f"FX{foreign}CAD"
        payload = await self._get(f"https://www.bankofcanada.ca/valet/observations/{series}/json",
                                  {"start_date": (as_of - timedelta(days=RECENT_DAYS)).isoformat(), "end_date": as_of.isoformat()})
        observations = payload.get("observations", []) if isinstance(payload, dict) else []
        # The latest published day on or before the snapshot date, kept with its own date.
        dated = [row for row in observations if isinstance(row, dict) and isinstance(row.get("d"), str)
                 and row["d"] <= as_of.isoformat()] if isinstance(observations, list) else []
        if not dated:
            return None
        latest = max(dated, key=lambda row: str(row["d"]))
        observed = date.fromisoformat(latest["d"])
        observation = latest.get(series, {})
        value = observation.get("v") if isinstance(observation, dict) else None
        if value is None:
            return None
        rate = Decimal(str(value))
        if not rate.is_finite() or rate <= 0:
            return None
        with localcontext() as context:
            context.prec = 40
            if from_currency == "CAD":
                rate = (Decimal(1) / rate).quantize(Decimal("0.0000000001"))
        return FX(from_currency=from_currency, to_currency=to_currency, rate=rate,
                  as_of=observed, source=f"Bank of Canada Valet {series}" +
                  (" (inverse, rounded to ten decimals)" if from_currency == "CAD" else ""),
                  captured_at=datetime.now(UTC), status="indicative")


class FakeFinancialProvider(PersonalFinancialProvider):
    """The external financial boundary for request/result and browser fixtures."""


SOURCE_ERRORS = (httpx.HTTPError, ValueError, KeyError, TypeError, ArithmeticError)


def contradictory_capture(as_of: date, captured_at: datetime | None) -> bool:
    return captured_at is not None and (
        captured_at.date() < as_of or captured_at > datetime.now(UTC)
    )


async def refresh_financial_data(snapshot: Snapshot, provider: FinancialProvider) -> FinancialEvidence:
    evidence = FinancialEvidence()
    if hasattr(provider, "reference") and getattr(provider.reference, "sponsor_holdings", None):
        for key, holdings_item in provider.reference.sponsor_holdings.items():
            evidence.sponsor_holdings[key] = holdings_item
    for position in snapshot.positions:
        if position.kind == "cash":
            continue
        try:
            identity = await provider.identity(position, snapshot.as_of)
            expected = (position.ticker, position.listing, position.currency, position.kind,
                        position.company_id, position.company_name)
            actual = (identity.ticker, identity.listing, identity.currency, identity.kind,
                      identity.company_id, identity.company_name)
            conflicting = any(supplied is not None and supplied != resolved
                              for supplied, resolved in zip(expected, actual, strict=True))
            if identity.status in {"verified", "supplied"} and conflicting:
                identity = identity.model_copy(update={"status": "conflicting"})
            if identity.status == "verified" and (
                not identity.ticker or not identity.listing or not identity.currency
                or not identity.kind or not identity.source_url
                or not identity.source_url.startswith(("https://", "http://"))
                or identity.captured_at is None or identity.as_of != snapshot.as_of
                or contradictory_capture(snapshot.as_of, identity.captured_at)
                or (position.kind == "stock" and (not identity.company_id or not identity.company_name))
            ):
                identity = identity.model_copy(update={"status": "unresolved"})
        except SOURCE_ERRORS:
            identity = Identity(status="unresolved", source="Unavailable source")
            evidence.issues.append(f"{position.id}: Identity source failed; value remains unknown.")
        evidence.identities[position.id] = identity
        try:
            quote = await provider.quote(position, snapshot.as_of)
        except QuoteUnavailable as reason:
            quote = None
            evidence.issues.append(f"{position.id}: {reason}")
        except SOURCE_ERRORS:
            quote = None
            evidence.issues.append(f"{position.id}: Price source failed; explicit broker-display fallback used if supplied.")
        if quote is not None:
            qualification = quote.qualification
            if quote.status != "manual" and (
                qualification is None or not qualification.personal_use_permitted
                or qualification.source != quote.source
                or quote.listing not in qualification.covered_listings
                or not qualification.terms_url.startswith(("https://", "http://"))
                or quote.captured_at is None or qualification.checked_on > quote.captured_at.date()
            ):
                evidence.issues.append(f"{position.id}: Price source coverage or terms are unqualified; broker-display fallback used if supplied.")
                quote = None
        if quote is None and position.mark and identity.ticker and identity.listing:
            quote = Quote(**position.mark.model_dump(), ticker=identity.ticker,
                          listing=identity.listing, currency=position.currency, status="manual")
            evidence.issues.append(f"{position.id}: No qualified provider quote; user-entered broker-display mark is provisional.")
        if quote and contradictory_capture(quote.as_of, quote.captured_at):
            quote = quote.model_copy(update={"status": "stale"})
            evidence.issues.append(f"{position.id}: Contradictory quote capture time; quote remains unusable.")
        evidence.quotes[position.id] = quote

        if position.kind == "etf":
            holdings = None
            if hasattr(provider, "sponsor_holdings"):
                try:
                    holdings = await provider.sponsor_holdings(position, snapshot.as_of)
                except SOURCE_ERRORS:
                    holdings = None
                    evidence.issues.append(f"{position.id}: Sponsor holdings source failed; look-through remains unknown.")
            if holdings is not None:
                if holdings.as_of != snapshot.as_of:
                    holdings = holdings.model_copy(update={"coverage": "stale", "status": "stale"})
                    evidence.issues.append(f"{position.id}: Sponsor holdings date differs from snapshot date; look-through is stale.")
                elif contradictory_capture(snapshot.as_of, holdings.captured_at):
                    holdings = holdings.model_copy(update={"coverage": "stale", "status": "stale"})
                    evidence.issues.append(f"{position.id}: Contradictory sponsor holdings capture time; look-through is stale.")
                evidence.sponsor_holdings[position.id] = holdings

    currencies = dict.fromkeys(p.currency for p in snapshot.positions
                                if p.currency != snapshot.reporting_currency)
    for currency in currencies:
        try:
            rate = await provider.fx(currency, snapshot.reporting_currency, snapshot.as_of)
        except SOURCE_ERRORS:
            rate = None
            evidence.issues.append(f"{currency}: FX source failed; explicit manual fallback used if supplied.")
        if rate and (rate.from_currency, rate.to_currency) != (currency, snapshot.reporting_currency):
            evidence.issues.append(f"{currency}: FX source returned a contradictory pair; value remains unknown.")
            continue
        if rate is None:
            rate = next((item for item in snapshot.fx if item.from_currency == currency
                         and item.to_currency == snapshot.reporting_currency), None)
            if rate:
                rate = rate.model_copy(update={"status": "manual" if rate.status == "indicative" else rate.status})
        if rate and contradictory_capture(rate.as_of, rate.captured_at):
            rate = rate.model_copy(update={"status": "stale"})
            evidence.issues.append(f"{currency}: Contradictory FX capture time; conversion remains unknown.")
        if rate:
            evidence.fx.append(rate)
    return evidence
