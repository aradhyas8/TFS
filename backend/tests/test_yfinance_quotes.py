"""yfinance is the primary quote source behind the daily cache; EODHD covers what it misses; analysis never calls either."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.financial_data import (
    EODHD_SOURCE,
    MARKET_TIME,
    YAHOO_SOURCE,
    PersonalFinancialProvider,
    QuoteCache,
)
from analyst.portfolio import PortfolioStore
from analyst.providers import FakeDataProvider, ModelTurn, ScriptedModel, ToolCall
from tests.test_analysis import recommendation
from tests.test_enrichment import PRICES, SHARES, Market, imported, reviewed

# Fictional Yahoo prices, deliberately different from the fake EODHD ones so the source is visible in totals.
YAHOO = {"AVGO": 376.51, "F": 12.12, "GOOG": 347.37, "HDB": 22.14, "IBN": 27.86, "INFY": 10.55, "NBIS": 237.15, "SHOP": 166.03, "CM.TO": 154.57}
STAMP = int(datetime.now(UTC).timestamp()) - 600


class Yahoo:
    """The yfinance boundary: one call per refresh with every due Yahoo symbol."""

    def __init__(self, missing: frozenset[str] = frozenset(), fail: bool = False, currency: dict[str, str] | None = None) -> None:
        self.missing, self.fail, self.currency = missing, fail, currency or {}
        self.calls: list[list[str]] = []

    def __call__(self, symbols: list[str]) -> dict[str, dict]:
        self.calls.append(list(symbols))
        if self.fail:
            raise RuntimeError("Invalid Crumb")
        return {s: {"price": YAHOO[s], "currency": self.currency.get(s, "CAD" if s.endswith(".TO") else "USD"), "time": STAMP,
                    "history": [("2026-10-06", YAHOO[s] - 1), ("2026-10-07", YAHOO[s])]}
                for s in symbols if s not in self.missing}


def app(tmp_path, market: Market, yahoo: Yahoo, reviews: int = 1) -> TestClient:
    provider = PersonalFinancialProvider(valet=True, transport=httpx.MockTransport(market), eodhd_key="eodhd-test-key",
                                         sec_agent="Test test@example.test", cache=QuoteCache(tmp_path / "market"), yahoo=yahoo)
    turns = []
    for n in range(reviews):
        turns += [ModelTurn(calls=[ToolCall(f"review-{n}", "review_portfolio", "{}")]), ModelTurn(answer=recommendation())]
    return TestClient(create_app(model=ScriptedModel(turns), data=FakeDataProvider(), financial=provider,
                                 portfolio_store=PortfolioStore(tmp_path / "portfolio")))


def test_all_nine_holdings_priced_by_one_yfinance_batch_and_reviewed_from_the_cache(tmp_path):
    market, yahoo = Market(), Yahoo()
    api = app(tmp_path, market, yahoo, reviews=2)
    saved = imported(api)
    refreshed = api.post("/api/market/refresh", json={}).json()

    # US tickers as-is, the TSX listing with .TO, all in one batch; EODHD is not touched.
    assert yahoo.calls == [["AVGO", "F", "GOOG", "HDB", "IBN", "INFY", "NBIS", "SHOP", "CM.TO"]]
    assert market.eodhd == ["search"]  # only the import-time TSX identity search
    assert len(refreshed["fetched"]) == 9 and refreshed["failed"] == [] and refreshed["remaining"] is None
    cm, avgo = refreshed["quotes"]["tfsa-cm"], refreshed["quotes"]["tfsa-avgo"]
    assert (cm["source"], cm["currency"], cm["status"], cm["value"]) == (YAHOO_SOURCE, "CAD", "delayed", "154.5700000000")
    assert (avgo["currency"], avgo["listing"], avgo["qualification"]["source"]) == ("USD", "XNAS", YAHOO_SOURCE)
    assert "not exchange data" in avgo["source"]

    # Persisted in the existing format and keys; daily closes beside it.
    stored = json.loads((tmp_path / "market" / "quotes.json").read_text())
    assert set(stored) == set(PRICES) and set(stored["CM.TO"]) == set(json.loads(json.dumps(cm)))
    history = QuoteCache(tmp_path / "market").read("history")
    assert history["CM.TO"]["symbol"] == "CM.TO" and history["CM.TO"]["daily_close"]["2026-10-07"] == "154.5700000000"

    for _ in range(2):
        portfolio = reviewed(api, saved["snapshot"])
    assert len(yahoo.calls) == 1 and market.eodhd == ["search"]  # reviews read the cache only

    rows = {row["supplied"]["ticker"]: row for row in portfolio["positions"]}
    usd = sum(Decimal(shares) * Decimal(str(YAHOO[ticker])) for ticker, shares in SHARES.items()) * Decimal("1.38")
    cad = 20 * Decimal("154.57")
    assert Decimal(portfolio["total_value"]) == usd + cad
    assert all(row["weight"] is not None for row in rows.values())
    assert abs(sum(Decimal(row["weight"]) for row in rows.values()) - 1) < Decimal("0.000001")  # weights are rounded
    assert {row["currency"]: Decimal(row["value"]) for row in portfolio["currency_exposure"]} == {"USD": usd, "CAD": cad}
    assert {row["quote_used"]["source"] for row in rows.values()} == {YAHOO_SOURCE}


def test_yfinance_failure_falls_back_to_eodhd(tmp_path):
    market, yahoo = Market(), Yahoo(fail=True)
    api = app(tmp_path, market, yahoo)
    imported(api)
    refreshed = api.post("/api/market/refresh", json={}).json()
    assert len(yahoo.calls) == 1 and market.eodhd.count("real-time") == 9
    assert len(refreshed["fetched"]) == 9
    assert {row["source"] for row in refreshed["quotes"].values()} == {EODHD_SOURCE}


def test_symbols_yfinance_misses_or_misprices_go_to_eodhd_alone(tmp_path):
    market = Market()
    yahoo = Yahoo(missing=frozenset({"NBIS"}), currency={"CM.TO": "USD"})  # a CAD listing quoted in USD is rejected
    api = app(tmp_path, market, yahoo)
    imported(api)
    refreshed = api.post("/api/market/refresh", json={}).json()
    assert market.eodhd.count("real-time") == 2
    sources = {pid: row["source"] for pid, row in refreshed["quotes"].items()}
    assert sources["tfsa-nbis"] == sources["tfsa-cm"] == EODHD_SOURCE
    assert {sources[pid] for pid in sources if pid not in {"tfsa-nbis", "tfsa-cm"}} == {YAHOO_SOURCE}
    assert refreshed["quotes"]["tfsa-cm"]["currency"] == "CAD"
    assert refreshed["message"].startswith("7 priced by Yahoo Finance (yfinance); EODHD fallback for 2")


def test_same_day_cache_means_zero_provider_calls(tmp_path):
    market, yahoo = Market(), Yahoo()
    api = app(tmp_path, market, yahoo)
    imported(api)
    api.post("/api/market/refresh", json={})
    again = api.post("/api/market/refresh", json={}).json()
    assert again["message"] == "Prices already fetched today; no provider calls used."
    assert len(again["fresh"]) == 9 and len(yahoo.calls) == 1 and market.eodhd == ["search"]


def test_stale_cache_is_refreshed_from_yfinance(tmp_path):
    market, yahoo = Market(), Yahoo()
    api = app(tmp_path, market, yahoo)
    imported(api)
    api.post("/api/market/refresh", json={})
    cache = QuoteCache(tmp_path / "market")
    yesterday = datetime.now(UTC) - timedelta(days=1)
    cache.put({symbol: row.model_copy(update={"captured_at": yesterday}) for symbol, row in cache.get().items()})
    refreshed = api.post("/api/market/refresh", json={}).json()
    assert len(yahoo.calls) == 2 and len(refreshed["fetched"]) == 9 and refreshed["fresh"] == []
    assert all(row.captured_at.astimezone(MARKET_TIME).date() == datetime.now(MARKET_TIME).date() for row in cache.get().values())


def test_missing_everywhere_stays_unknown(tmp_path):
    market, yahoo = Market(missing=frozenset({"F.US"})), Yahoo(missing=frozenset({"F"}))
    api = app(tmp_path, market, yahoo)
    saved = imported(api)
    refreshed = api.post("/api/market/refresh", json={}).json()
    assert refreshed["failed"] == ["F.US"] and "tfsa-f" not in refreshed["quotes"]
    portfolio = reviewed(api, saved["snapshot"])
    assert portfolio["total_value"] is None
    assert "1 holding could not be priced from the cache; use Refresh prices (F)." in portfolio["qualifications"]


def test_source_currency_and_timestamp_survive_the_cache(tmp_path):
    market, yahoo = Market(), Yahoo()
    api = app(tmp_path, market, yahoo)
    imported(api, "account,ticker,shares,currency\nTFSA,CM,20,CAD\nTFSA,AVGO,1,USD\n")
    before = datetime.now(UTC)
    api.post("/api/market/refresh", json={})
    cached = QuoteCache(tmp_path / "market").get()
    traded = datetime.fromtimestamp(STAMP, MARKET_TIME).date()
    for symbol, currency in {"CM.TO": "CAD", "AVGO.US": "USD"}.items():
        row = cached[symbol]
        assert (row.source, row.currency, row.as_of, row.status, row.basis) == (YAHOO_SOURCE, currency, traded, "delayed", "unadjusted")
        assert row.captured_at is not None and row.captured_at >= before - timedelta(seconds=1)
        assert row.qualification is not None and row.qualification.source == YAHOO_SOURCE


def test_yahoo_quotes_adapts_yfinance_output_and_drops_failures(monkeypatch):
    import pandas as pd
    import yfinance

    from analyst.financial_data import yahoo_quotes

    class Ticker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **_: object) -> pd.DataFrame:
            if self.symbol == "BAD":
                raise RuntimeError("Invalid Crumb")
            return pd.DataFrame({"Close": [153.0, 154.57]}, index=pd.to_datetime(["2026-10-06", "2026-10-07"]).tz_localize("America/Toronto"))

        def get_history_metadata(self) -> dict:
            return {"regularMarketPrice": 154.57, "currency": "CAD",
                    "regularMarketTime": pd.Timestamp("2026-10-07 16:00:01-0400", tz="America/Toronto")}

    monkeypatch.setattr(yfinance, "Ticker", Ticker)
    rows = yahoo_quotes(["CM.TO", "BAD"])
    assert set(rows) == {"CM.TO"}
    assert rows["CM.TO"] == {"price": 154.57, "currency": "CAD", "time": 1791403201,
                             "history": [("2026-10-06", 153.0), ("2026-10-07", 154.57)]}
