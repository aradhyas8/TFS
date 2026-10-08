"""A real-style portfolio is identified once, priced through a daily quote cache and reviewed with no EODHD calls."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.financial_data import MARKET_TIME, PersonalFinancialProvider, QuoteCache
from analyst.portfolio import PortfolioStore
from analyst.providers import FakeDataProvider, ModelTurn, ScriptedModel, ToolCall
from analyst.schemas import SavedPortfolio
from tests.test_analysis import recommendation

HOLDINGS = """account,ticker,shares,average_cost,currency
TFSA,AVGO,12.0902,385.71,USD
TFSA,F,4.1689,20.24,USD
TFSA,GOOG,2.0019,249.55,USD
TFSA,HDB,8.116,30.60,USD
TFSA,IBN,4.0276,21.93,USD
TFSA,INFY,2.0721,23.05,USD
TFSA,NBIS,0.5082,247.02,USD
TFSA,SHOP,10,79.98,USD
TFSA,CM,20,158.91,CAD
"""
SHARES = {"AVGO": "12.0902", "F": "4.1689", "GOOG": "2.0019", "HDB": "8.116", "IBN": "4.0276", "INFY": "2.0721", "NBIS": "0.5082", "SHOP": "10"}
# SEC's own spelling, including its /STATE/ suffix. CIKs as served live.
SEC = [[1730168, "Broadcom Inc.", "AVGO", "Nasdaq"], [37996, "FORD MOTOR CO", "F", "NYSE"], [1652044, "Alphabet Inc.", "GOOG", "Nasdaq"],
       [1144967, "HDFC BANK LTD", "HDB", "NYSE"], [1103838, "ICICI BANK LTD", "IBN", "NYSE"], [1067491, "Infosys Ltd", "INFY", "NYSE"],
       [1513845, "Nebius Group N.V.", "NBIS", "Nasdaq"], [1594805, "SHOPIFY INC.", "SHOP", "Nasdaq"],
       [1045520, "CANADIAN IMPERIAL BANK OF COMMERCE /CAN/", "CM", "NYSE"]]
# Fictional delayed prices.
PRICES = {"AVGO.US": 340, "F.US": 12, "GOOG.US": 250, "HDB.US": 35, "IBN.US": 30, "INFY.US": 18, "NBIS.US": 100, "SHOP.US": 150, "CM.TO": 110}
TODAY = datetime.now(MARKET_TIME).date()
# SEC submissions records, trimmed: what a registrant files says what it is.
OPERATING = {"entityType": "operating", "sic": "3674", "filings": {"recent": {"form": ["10-K", "10-Q", "8-K"]}}}


class Market:
    """EODHD free plan (20 requests a day), SEC and Bank of Canada behind one fake transport."""

    def __init__(self, used: int = 0, missing: frozenset[str] = frozenset(), sec_down: bool = False,
                 sec: list | None = None, entities: dict[int, dict] | None = None) -> None:
        self.used, self.missing, self.sec_down = used, missing, sec_down
        self.sec, self.entities = SEC if sec is None else sec, entities or {}
        self.eodhd: list[str] = []  # one entry per counted EODHD request unit

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.sec.gov":
            assert request.headers["User-Agent"] == "Test test@example.test"
            return httpx.Response(403) if self.sec_down else httpx.Response(200, json={"fields": ["cik", "name", "ticker", "exchange"], "data": self.sec})
        if request.url.host == "data.sec.gov":
            assert request.headers["User-Agent"] == "Test test@example.test"
            cik = int(request.url.path.removeprefix("/submissions/CIK").removesuffix(".json"))
            return httpx.Response(200, json=self.entities.get(cik, OPERATING))
        if request.url.host == "www.bankofcanada.ca":
            return httpx.Response(200, json={"observations": [{"d": (TODAY - timedelta(days=1)).isoformat(), "FXUSDCAD": {"v": "1.38"}}]})
        assert request.url.host == "eodhd.com" and request.url.params["api_token"] == "eodhd-test-key"
        path = request.url.path.removeprefix("/api/")
        if path == "user":  # not counted by EODHD
            return httpx.Response(200, json={"apiRequests": self.used, "apiRequestsDate": datetime.now(UTC).date().isoformat(), "dailyRateLimit": 20})
        realtime = path.startswith("real-time/")
        symbols = [path.removeprefix("real-time/"), *filter(None, request.url.params.get("s", "").split(","))] if realtime else [path]
        if self.used + len(symbols) > 20:
            return httpx.Response(402, text="You exceeded your daily API requests limit.")
        self.used += len(symbols)
        self.eodhd += ["real-time" if realtime else "search"] * len(symbols)
        if not realtime:
            ticker, exchange = path.removeprefix("search/"), request.url.params["exchange"]
            listed = exchange == "TO" and f"{ticker}.TO" in PRICES
            return httpx.Response(200, json=[{"Code": ticker, "Exchange": "TO", "Name": "Canadian Imperial Bank Of Commerce", "Type": "Common Stock",
                                              "Country": "Canada", "Currency": "CAD", "ISIN": "CA1360691010"}] if listed else [])
        stamp = int(datetime.now(UTC).timestamp()) - 1200
        rows = [{"code": s, "timestamp": stamp, "close": "NA" if s in self.missing else PRICES[s]} for s in symbols]
        return httpx.Response(200, json=rows if len(rows) > 1 else rows[0])


def app(tmp_path, market: Market, reviews: int = 1) -> TestClient:
    provider = PersonalFinancialProvider(valet=True, transport=httpx.MockTransport(market), eodhd_key="eodhd-test-key",
                                         sec_agent="Test test@example.test", cache=QuoteCache(tmp_path / "market"))
    turns = []
    for n in range(reviews):
        turns += [ModelTurn(calls=[ToolCall(f"review-{n}", "review_portfolio", "{}")]), ModelTurn(answer=recommendation())]
    return TestClient(create_app(model=ScriptedModel(turns), data=FakeDataProvider(), financial=provider,
                                 portfolio_store=PortfolioStore(tmp_path / "portfolio")))


def imported(api: TestClient, csv: str = HOLDINGS) -> dict:
    response = api.post("/api/portfolio/import", json={"csv": csv, "as_of": TODAY.isoformat(), "reporting_currency": "CAD"})
    assert response.status_code == 200, response.text
    return response.json()


def reviewed(api: TestClient, snapshot: dict) -> dict:
    response = api.post("/api/analyze", json={"question": "Review my portfolio", "portfolio": snapshot})
    assert response.status_code == 200, response.text
    return response.json()["portfolio"]


def test_identity_once_quotes_once_a_day_and_reviews_cost_no_eodhd_calls(tmp_path):
    market = Market()
    api = app(tmp_path, market, reviews=3)
    saved = imported(api)
    # SEC identifies the eight US holdings for free; only the TSX listing costs one EODHD search.
    assert market.eodhd == ["search"]
    assert saved["unresolved"] == []
    positions = {row["ticker"]: row for row in saved["snapshot"]["positions"]}
    assert (positions["SHOP"]["listing"], positions["F"]["listing"]) == ("XNAS", "XNYS")
    assert (positions["CM"]["listing"], positions["CM"]["currency"], positions["CM"]["company_id"]) == ("XTSE", "CAD", "CIK0001045520")

    refreshed = api.post("/api/market/refresh", json={}).json()
    assert len(refreshed["fetched"]) == 9 and refreshed["remaining"] == 10
    assert market.eodhd.count("real-time") == 9
    assert (refreshed["quotes"]["tfsa-cm"]["status"], refreshed["quotes"]["tfsa-cm"]["currency"]) == ("delayed", "CAD")

    before = len(market.eodhd)
    for _ in range(3):
        portfolio = reviewed(api, saved["snapshot"])
    assert api.post("/api/market/refresh", json={}).json()["message"] == "Prices already fetched today; no provider calls used."
    assert len(market.eodhd) == before  # three reviews and a same-day refresh: zero EODHD calls

    rows = {row["supplied"]["ticker"]: row for row in portfolio["positions"]}
    assert {row["identity_status"] for row in rows.values()} == {"verified"}
    assert rows["AVGO"]["quote_used"]["status"] == "delayed"  # never presented as live
    usd = sum(Decimal(shares) * PRICES[f"{ticker}.US"] for ticker, shares in SHARES.items()) * Decimal("1.38")
    assert Decimal(portfolio["total_value"]) == usd + 20 * 110
    assert all(row["weight"] is not None for row in rows.values())
    assert {row["currency"]: Decimal(row["value"]) for row in portfolio["currency_exposure"]} == {"USD": usd, "CAD": 2200}
    assert portfolio["accounts"][0]["total_value"] == portfolio["total_value"]
    assert len(portfolio["direct_companies"]) == 9
    assert "eodhd-test-key" not in str(saved) + str(refreshed) + str(portfolio)

    forced = api.post("/api/market/refresh", json={"force": True}).json()
    assert len(forced["fetched"]) == 9 and market.eodhd.count("real-time") == 18


def test_daily_limit_fetches_what_fits_and_leaves_the_rest_unknown(tmp_path):
    market = Market(used=15)
    api = app(tmp_path, market)
    saved = imported(api)  # the TSX search uses one request: 4 left
    refreshed = api.post("/api/market/refresh", json={}).json()
    assert (len(refreshed["fetched"]), len(refreshed["skipped"]), refreshed["remaining"]) == (4, 5, 0)
    assert "daily limit" in refreshed["message"]
    portfolio = reviewed(api, saved["snapshot"])
    assert portfolio["total_value"] is None
    assert any(note.startswith("5 holdings could not be priced from the cache; use Refresh prices") for note in portfolio["qualifications"])
    calls = len(market.eodhd)
    assert api.post("/api/market/refresh", json={"force": True}).json()["fetched"] == []  # exhausted: nothing more today
    assert len(market.eodhd) == calls


def test_refused_batch_keeps_the_cache_and_says_why(tmp_path):
    market = Market()
    api = app(tmp_path, market)
    imported(api)
    api.post("/api/market/refresh", json={})
    market.used = 15  # the plan counter says 5 left, but EODHD refuses the batch anyway

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402) if request.url.path.startswith("/api/real-time") else market(request)
    provider = PersonalFinancialProvider(transport=httpx.MockTransport(refuse), eodhd_key="eodhd-test-key", cache=QuoteCache(tmp_path / "market"))
    forced = TestClient(create_app(financial=provider, portfolio_store=PortfolioStore(tmp_path / "portfolio"))).post(
        "/api/market/refresh", json={"force": True}).json()
    assert forced["fetched"] == [] and "limit" in forced["message"]
    assert len(forced["quotes"]) == 9  # the earlier cache is kept


def test_missing_prices_stay_unknown_and_are_summarized_once(tmp_path):
    api = app(tmp_path, Market(missing=frozenset({"NBIS.US", "F.US"})))
    saved = imported(api)
    assert api.post("/api/market/refresh", json={}).json()["failed"] == ["F.US", "NBIS.US"]
    portfolio = reviewed(api, saved["snapshot"])
    assert "2 holdings could not be priced from the cache; use Refresh prices (F, NBIS)." in portfolio["qualifications"]
    assert portfolio["total_value"] is None


def test_quotes_from_an_earlier_day_are_labelled_cached(tmp_path):
    api = app(tmp_path, Market())
    saved = imported(api)
    api.post("/api/market/refresh", json={})
    cache = QuoteCache(tmp_path / "market")
    yesterday = datetime.now(UTC) - timedelta(days=1)
    # Backdate the source qualification too: a quote captured before it would be rejected outright.
    cache.put({symbol: row.model_copy(update={"captured_at": yesterday, "as_of": yesterday.astimezone(MARKET_TIME).date(),
                                              "qualification": row.qualification.model_copy(update={"checked_on": yesterday.date() - timedelta(days=1)})})
               for symbol, row in cache.get().items()})
    rows = reviewed(api, saved["snapshot"])["positions"]
    assert {row["quote_used"]["status"] for row in rows} == {"cached"}
    assert all(row["value"] is not None and row["source_inputs_usable"] is False for row in rows)


def test_ambiguous_listing_asks_with_only_the_matching_listings(tmp_path):
    api = app(tmp_path, Market())
    saved = imported(api, "account,ticker,shares\nTFSA,CM,20\nTFSA,ZZZZ,1\n")
    unresolved = {row["ticker"]: row for row in saved["unresolved"]}
    assert unresolved["CM"]["candidates"] == ["XNYS", "XTSE"]
    assert unresolved["ZZZZ"]["candidates"] == []
    answered = api.post("/api/portfolio/identify", json={"account_id": "tfsa", "ticker": "CM", "listing": "XTSE"}).json()
    cm = next(row for row in answered["snapshot"]["positions"] if row["ticker"] == "CM")
    assert (cm["listing"], cm["currency"], cm["kind"], cm["company_name"]) == ("XTSE", "CAD", "stock", "Canadian Imperial Bank Of Commerce")


def test_sec_outage_leaves_tsx_identity_supplied_not_verified(tmp_path):
    api = app(tmp_path, Market(sec_down=True))
    saved = imported(api, "account,ticker,shares,currency\nTFSA,CM,20,CAD\n")
    [cm] = saved["snapshot"]["positions"]
    assert (cm["listing"], cm["company_id"]) == ("XTSE", "ISIN-CA1360691010")
    assert reviewed(api, saved["snapshot"])["positions"][0]["identity_status"] == "supplied"


def test_a_portfolio_saved_before_the_resolver_improved_is_upgraded_on_load(tmp_path):
    # As saved by an older resolver: listings from the user's answers, no issuer identities.
    listings = {"AVGO": "XNYS", "F": "XNYS", "GOOG": "XNAS", "HDB": "XNYS", "IBN": "XNYS", "INFY": "XNYS", "NBIS": "XNYS", "SHOP": "XTSE"}
    positions = [{"id": f"tfsa-{t.lower()}", "account_id": "tfsa", "kind": "stock", "currency": "USD", "ticker": t, "listing": listing,
                  "shares": SHARES[t]} for t, listing in listings.items()]
    positions.append({"id": "tfsa-cm", "account_id": "tfsa", "kind": "stock", "currency": "CAD", "ticker": "CM", "listing": "XTSE", "shares": "20"})
    old = SavedPortfolio.model_validate({"snapshot": {"as_of": "2026-10-06", "reporting_currency": "CAD", "accounts": [{"id": "tfsa", "name": "TFSA"}],
                                                      "positions": positions}, "average_costs": {"tfsa-cm": "158.91"},
                                         "settings": {"single_company_cap": "0.2"}})
    store = PortfolioStore(tmp_path / "portfolio")
    store.save(old)

    # EODHD quota used up: SEC alone upgrades the US holdings; CM waits without losing anything.
    market = Market(used=20)
    loaded = app(tmp_path, market).get("/api/portfolio").json()
    by_ticker = {row["ticker"]: row for row in loaded["snapshot"]["positions"]}
    assert market.eodhd == []
    assert by_ticker["AVGO"]["listing"] == "XNAS" and by_ticker["AVGO"]["company_id"] == "CIK0001730168"
    assert by_ticker["SHOP"]["listing"] == "XNAS" and by_ticker["SHOP"]["currency"] == "USD"
    assert all(by_ticker[t]["company_id"] for t in listings) and by_ticker["CM"]["company_id"] is None
    assert {t: row["shares"] for t, row in by_ticker.items() if t != "CM"} == SHARES
    assert (loaded["snapshot"]["as_of"], loaded["average_costs"], loaded["settings"]) == ("2026-10-06", {"tfsa-cm": "158.91"}, old.settings.model_dump(mode="json"))
    assert store.get().snapshot.positions == SavedPortfolio.model_validate(loaded).snapshot.positions

    # With quota, CM resolves through the Canadian path in one search; the US holdings are not looked up again.
    market = Market()
    cm = next(row for row in app(tmp_path, market).get("/api/portfolio").json()["snapshot"]["positions"] if row["ticker"] == "CM")
    assert market.eodhd == ["search"] and (cm["listing"], cm["company_id"]) == ("XTSE", "CIK0001045520")
