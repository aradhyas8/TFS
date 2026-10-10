"""OpenFIGI fills identity only when SEC cannot, never guesses a listing, keeps the user's currency and is cached."""

import json

import httpx
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.financial_data import OPENFIGI_URL, PersonalFinancialProvider, QuoteCache
from analyst.portfolio import PortfolioStore
from analyst.schemas import SavedPortfolio
from tests.test_enrichment import SEC, TODAY, Market


def figi(exch: str, ticker: str, name: str, kind: str, composite: str, share: str) -> dict:
    security = ("ETP", "Mutual Fund") if kind == "etf" else ("Common Stock", "Common Stock")
    return {"exchCode": exch, "ticker": ticker, "name": name, "securityType": security[0], "securityType2": security[1],
            "marketSector": "Equity", "figi": f"{composite}-{exch}", "compositeFIGI": composite, "shareClassFIGI": share}


CIBC = "CAN IMPERIAL BK OF COMMERCE"
# Trimmed from live OpenFIGI answers on 2026-10-08: every venue is listed, not just the primary listing.
OPENFIGI = {
    "CM": [figi("CN", "CM", CIBC, "stock", "BBG000BY34Q2", "BBG001S5YBB1"), figi("CT", "CM", CIBC, "stock", "BBG000BY34Q2", "BBG001S5YBB1"),
           figi("US", "CM", CIBC, "stock", "BBG000FKTHQ1", "BBG001S5YBB1"), figi("UN", "CM", CIBC, "stock", "BBG000FKTHQ1", "BBG001S5YBB1"),
           figi("UP", "CM", CIBC, "stock", "BBG000FKTHQ1", "BBG001S5YBB1")],
    "XIU": [figi("CN", "XIU", "ISHARES S&P/TSX 60 INDEX ETF", "etf", "BBG000C1JPY0", "BBG001SB5WK4"),
            figi("CT", "XIU", "ISHARES S&P/TSX 60 INDEX ETF", "etf", "BBG000C1JPY0", "BBG001SB5WK4")],
    "SPY": [figi(exch, "SPY", "SS SPDR S&P 500 ETF TRUST-US", "etf", "BBG000BDTBL9", "BBG001S72SM3") for exch in ("US", "UN", "UP", "UA")],
    "QQQ": [*(figi(exch, "QQQ", "INVESCO QQQ TRUST SERIES 1", "etf", "BBG000BSWKH7", "BBG001S9GN63") for exch in ("US", "UN", "UP", "UQ")),
            figi("CN", "QQQ", "QUESTCORP MINING INC", "stock", "BBG01KJGZ7T3", "BBG01KJGZ900"),
            figi("CF", "QQQ", "QUESTCORP MINING INC", "stock", "BBG01KJGZ7T3", "BBG01KJGZ900")],
    "AVGO": [figi(exch, "AVGO", "BROADCOM INC", "stock", "BBG00KHY5S69", "BBG00KHY5SY8") for exch in ("US", "UN", "UW")],
}


# SEC lists these trusts in its company ticker file. As served live on 2026-10-08: the SPDR S&P 500 trust has no SIC
# and files investment-company forms; the gold trust files 10-Ks under SIC 6221 (commodity contracts).
SEC_TRUSTS = [[884394, "SPDR S&P 500 ETF TRUST", "SPY", "NYSE"], [1222333, "SPDR GOLD TRUST", "GLD", "NYSE"]]
ENTITIES = {884394: {"entityType": "other", "sic": "", "filings": {"recent": {"form": ["N-CEN", "NPORT-P", "485BPOS", "24F-2NT", "497"]}}},
            1222333: {"entityType": "operating", "sic": "6221", "filings": {"recent": {"form": ["10-K", "10-Q", "8-K", "FWP"]}}}}
OPENFIGI["GLD"] = [figi(exch, "GLD", "SPDR GOLD SHARES", "etf", "BBG000CRF6Q8", "BBG001SDZ8F7") for exch in ("US", "UN", "UP")]


class Sources:
    """SEC, EODHD and Bank of Canada from the enrichment fake, plus OpenFIGI."""

    def __init__(self, **market: object) -> None:
        self.market = Market(sec=[*SEC, *SEC_TRUSTS], entities=ENTITIES, **market)  # type: ignore[arg-type]
        self.openfigi: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) != OPENFIGI_URL:
            return self.market(request)
        [job] = json.loads(request.content)
        assert job["idType"] == "TICKER" and job["marketSecDes"] == "Equity" and "X-OPENFIGI-APIKEY" not in request.headers
        self.openfigi.append(job["idValue"])
        rows = OPENFIGI.get(job["idValue"])
        return httpx.Response(200, json=[{"data": rows} if rows else {"warning": "No identifier found."}])


def app(tmp_path, sources: Sources, eodhd: bool = False) -> TestClient:
    provider = PersonalFinancialProvider(transport=httpx.MockTransport(sources), eodhd_key="eodhd-test-key" if eodhd else "",
                                         sec_agent="Test test@example.test", cache=QuoteCache(tmp_path / "market"), openfigi=True)
    return TestClient(create_app(financial=provider, portfolio_store=PortfolioStore(tmp_path / "portfolio")))


def imported(api: TestClient, rows: str) -> dict:
    response = api.post("/api/portfolio/import", json={"csv": "account,ticker,shares,currency\n" + rows, "as_of": TODAY.isoformat(),
                                                       "reporting_currency": "CAD"})
    assert response.status_code == 200, response.text
    return response.json()


def positions(saved: dict) -> dict[str, dict]:
    return {row["ticker"]: row for row in saved["snapshot"]["positions"]}


def test_cm_on_the_tsx_is_verified_through_the_shared_share_class(tmp_path):
    sources = Sources()
    cm = positions(imported(app(tmp_path, sources), "TFSA,CM.TO,20,CAD\n"))["CM"]
    assert (cm["listing"], cm["currency"], cm["kind"]) == ("XTSE", "CAD", "stock")
    # OpenFIGI's TSX and US composites share one share-class FIGI; SEC registers the US ticker.
    assert (cm["company_id"], cm["company_name"]) == ("CIK0001045520", "CANADIAN IMPERIAL BANK OF COMMERCE /CAN/")
    assert sources.openfigi == ["CM"] and sources.market.eodhd == []


def test_bare_cad_cm_asks_only_for_the_exchange_without_eodhd_and_uses_eodhd_when_available(tmp_path):
    sources = Sources()
    api = app(tmp_path / "a", sources)
    [waiting] = imported(api, "TFSA,CM,20,CAD\n")["unresolved"]
    # OpenFIGI's venue codes do not say which Canadian exchange lists CM, so none is guessed.
    assert (waiting["listing"], waiting["currency"], waiting["kind"], waiting["candidates"]) == (None, "CAD", "stock", [])
    answered = api.post("/api/portfolio/identify", json={"account_id": "tfsa", "ticker": "CM", "listing": "XTSE"}).json()
    assert positions(answered)["CM"]["company_id"] == "CIK0001045520"
    assert sources.openfigi == ["CM"]  # the answer re-used the cached identity

    sources = Sources()
    cm = positions(imported(app(tmp_path / "b", sources, eodhd=True), "TFSA,CM,20,CAD\n"))["CM"]
    assert sources.openfigi == ["CM"] and sources.market.eodhd == ["search"]  # existing fallback names the listing
    assert (cm["listing"], cm["company_id"]) == ("XTSE", "CIK0001045520")


def test_sec_resolved_us_stock_never_calls_openfigi(tmp_path):
    sources = Sources()
    api = app(tmp_path, sources)
    avgo = positions(imported(api, "TFSA,AVGO,1,USD\n"))["AVGO"]
    assert (avgo["listing"], avgo["company_id"]) == ("XNAS", "CIK0001730168")
    for _ in range(2):
        api.get("/api/portfolio")
    assert sources.openfigi == []


def test_canadian_etf(tmp_path):
    sources = Sources()
    xiu = positions(imported(app(tmp_path, sources), "TFSA,XIU.TO,10,CAD\n"))["XIU"]
    assert (xiu["listing"], xiu["currency"], xiu["kind"], xiu["company_name"], xiu["company_id"]) == (
        "XTSE", "CAD", "etf", "ISHARES S&P/TSX 60 INDEX ETF", None)


def test_us_etfs_nasdaq_resolves_and_arca_asks_for_the_exchange(tmp_path):
    sources = Sources()
    api = app(tmp_path, sources)
    saved = imported(api, "TFSA,QQQ,3,USD\nTFSA,SPY,2,USD\n")
    qqq = positions(saved)["QQQ"]
    assert (qqq["listing"], qqq["currency"], qqq["kind"], qqq["company_name"]) == ("XNAS", "USD", "etf", "INVESCO QQQ TRUST SERIES 1")
    # SEC lists SPY, but as an investment company: an ETF under SEC's name, never a verified stock on SEC's "NYSE".
    # OpenFIGI shows it on NYSE, Arca and others without saying which lists it, so only the exchange is asked.
    [spy] = saved["unresolved"]
    assert (spy["ticker"], spy["listing"], spy["currency"], spy["kind"], spy["candidates"]) == ("SPY", None, "USD", "etf", [])
    assert sources.openfigi == ["QQQ", "SPY"] and sources.market.eodhd == []
    answered = positions(api.post("/api/portfolio/identify", json={"account_id": "tfsa", "ticker": "SPY", "listing": "XNYS"}).json())["SPY"]
    assert (answered["kind"], answered["listing"], answered["company_name"], answered["company_id"]) == ("etf", "XNYS", "SPDR S&P 500 ETF TRUST", None)


def test_commodity_trust_sec_cannot_classify_is_typed_by_openfigi(tmp_path):
    sources = Sources()
    [gld] = imported(app(tmp_path, sources), "TFSA,GLD,1,USD\n")["unresolved"]
    assert (gld["kind"], gld["currency"], gld["listing"]) == ("etf", "USD", None)
    assert sources.openfigi == ["GLD"]


def test_normal_us_stock_stays_a_verified_sec_company(tmp_path):
    sources = Sources()
    avgo = positions(imported(app(tmp_path, sources), "TFSA,AVGO,1,USD\n"))["AVGO"]
    assert (avgo["kind"], avgo["listing"], avgo["company_id"], avgo["company_name"]) == ("stock", "XNAS", "CIK0001730168", "Broadcom Inc.")
    assert sources.openfigi == []


def test_ambiguous_ticker_lists_what_is_known_and_guesses_nothing(tmp_path):
    sources = Sources()
    saved = imported(app(tmp_path, sources), "TFSA,QQQ,3,\n")  # no currency: the US ETF and a Canadian miner both match
    [qqq] = saved["unresolved"]
    assert saved["snapshot"]["positions"] == [] and qqq["candidates"] == ["XNAS"] and qqq["listing"] is None


def test_unknown_ticker_stays_unresolved_and_is_not_asked_again_today(tmp_path):
    sources = Sources()
    api = app(tmp_path, sources)
    [zzzz] = imported(api, "TFSA,ZZZZ,1,USD\n")["unresolved"]
    assert (zzzz["listing"], zzzz["kind"], zzzz["candidates"]) == (None, None, [])
    api.get("/api/portfolio")
    assert sources.openfigi == ["ZZZZ"]


def test_repeated_loads_use_the_cached_identity(tmp_path):
    sources = Sources()
    imported(app(tmp_path, sources), "TFSA,SPY,2,USD\n")  # stays waiting for its exchange, so every load looks it up
    for _ in range(3):
        assert app(tmp_path, sources).get("/api/portfolio").json()["unresolved"][0]["kind"] == "etf"
    assert sources.openfigi == ["SPY"]
    cached = QuoteCache(tmp_path / "market").read("identities")["SPY"]["rows"]
    assert {row["exchCode"] for row in cached} == {"US"}  # only composites and Nasdaq tiers are kept
    assert set(cached[0]) == {"ticker", "name", "exchCode", "securityType", "securityType2", "figi", "compositeFIGI", "shareClassFIGI"}


def test_saved_portfolio_is_enriched_without_reimport_and_keeps_the_users_currency(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio")
    store.save(SavedPortfolio.model_validate({
        "snapshot": {"as_of": "2026-10-06", "reporting_currency": "CAD", "accounts": [{"id": "tfsa", "name": "TFSA"}],
                     "positions": [{"id": "tfsa-cm", "account_id": "tfsa", "kind": "stock", "currency": "CAD", "ticker": "CM",
                                    "listing": "XTSE", "shares": "20"}]},
        "unresolved": [{"account_id": "tfsa", "ticker": "XIU", "shares": "5", "currency": "USD"}]}))
    sources = Sources()
    loaded = app(tmp_path, sources).get("/api/portfolio").json()
    cm = positions(loaded)["CM"]
    assert (cm["listing"], cm["currency"], cm["company_id"]) == ("XTSE", "CAD", "CIK0001045520")
    # XIU is a CAD listing on OpenFIGI, but the user said USD: no US match, and the currency is not switched.
    [xiu] = loaded["unresolved"]
    assert (xiu["currency"], xiu["listing"], xiu["candidates"]) == ("USD", None, [])
    assert sorted(sources.openfigi) == ["CM", "XIU"]
