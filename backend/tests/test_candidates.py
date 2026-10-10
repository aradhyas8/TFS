from decimal import Decimal

from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.portfolio import PortfolioStore
from analyst.schemas import (
    Identity,
    Position,
    Quote,
    SavedPortfolio,
    Snapshot,
)


def sample_snapshot() -> Snapshot:
    return Snapshot(
        as_of="2026-09-30",
        reporting_currency="USD",
        accounts=[{"id": "tfsa", "name": "TFSA"}],
        positions=[
            Position(
                id="held-1",
                account_id="tfsa",
                kind="stock",
                currency="USD",
                ticker="AAPL",
                listing="XNAS",
                shares=Decimal(10),
            )
        ],
    )


class StubProvider:
    def __init__(self, identities: dict[str, list[Identity]], quotes: dict[str, Quote] | None = None) -> None:
        self.identities = identities
        self.quotes = quotes or {}
        self.refreshed: list[list[Position]] = []

    async def lookup(self, ticker: str, listing: str | None, currency: str | None, as_of: object) -> list[Identity]:
        matches = self.identities.get(ticker, [])
        if listing:
            matches = [m for m in matches if m.listing == listing]
        if currency:
            matches = [m for m in matches if m.currency == currency]
        return matches

    async def refresh_quotes(self, positions: list[Position], *, force: bool = False) -> dict[str, object]:
        self.refreshed.append(positions)
        return {"fetched": [p.id for p in positions], "message": "Quotes refreshed."}


class StubCache:
    def __init__(self, quotes: dict[str, Quote]) -> None:
        self._quotes = quotes

    def get(self) -> dict[str, Quote]:
        return self._quotes


def test_candidate_lookup_single_stock_match(tmp_path):
    identities = {
        "NVDA": [
            Identity(
                status="verified",
                ticker="NVDA",
                listing="XNAS",
                currency="USD",
                kind="stock",
                company_id="CIK0001045810",
                company_name="NVIDIA CORP",
                source="SEC",
            )
        ]
    }
    provider = StubProvider(identities)
    quote = Quote(
        ticker="NVDA",
        listing="XNAS",
        currency="USD",
        value=Decimal("120.50"),
        as_of="2026-09-30",
        source="yfinance",
        status="delayed",
    )
    provider.cache = StubCache({"NVDA.US": quote})

    store = PortfolioStore(tmp_path / "portfolio")
    store.save(SavedPortfolio(snapshot=sample_snapshot()))

    client = TestClient(create_app(financial=provider, portfolio_store=store))
    resp = client.post("/api/candidates", json={"ticker": "NVDA"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["position"] is not None
    assert data["position"]["id"] == "candidate-NVDA-XNAS"
    assert data["position"]["ticker"] == "NVDA"
    assert data["position"]["listing"] == "XNAS"
    assert data["position"]["kind"] == "stock"
    assert data["position"]["shares"] == "0"
    assert data["position"]["account_id"] == "tfsa"
    assert data["position"]["mark"]["value"] == "120.50"
    assert data["is_fund"] is False
    assert data["unresolved"] is None
    assert len(provider.refreshed) == 1


def test_candidate_lookup_multiple_matches_disambiguation(tmp_path):
    identities = {
        "BMO": [
            Identity(
                status="verified",
                ticker="BMO",
                listing="XNYS",
                currency="USD",
                kind="stock",
                company_id="CIK0000009279",
                company_name="BANK OF MONTREAL",
                source="SEC",
            ),
            Identity(
                status="verified",
                ticker="BMO",
                listing="XTSE",
                currency="CAD",
                kind="stock",
                company_id="CIK0000009279",
                company_name="BANK OF MONTREAL",
                source="TSX",
            ),
        ]
    }
    provider = StubProvider(identities)
    store = PortfolioStore(tmp_path / "portfolio")
    store.save(SavedPortfolio(snapshot=sample_snapshot()))

    client = TestClient(create_app(financial=provider, portfolio_store=store))
    resp = client.post("/api/candidates", json={"ticker": "BMO"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["position"] is None
    assert data["unresolved"] is not None
    assert data["unresolved"]["reason"] == "multiple"
    assert len(data["unresolved"]["listings"]) == 2
    listings = {alt["listing"] for alt in data["unresolved"]["listings"]}
    assert listings == {"XNYS", "XTSE"}


def test_candidate_lookup_with_suffix_disambiguates_single_match(tmp_path):
    identities = {
        "BMO": [
            Identity(
                status="verified",
                ticker="BMO",
                listing="XNYS",
                currency="USD",
                kind="stock",
                company_id="CIK0000009279",
                company_name="BANK OF MONTREAL",
                source="SEC",
            ),
            Identity(
                status="verified",
                ticker="BMO",
                listing="XTSE",
                currency="CAD",
                kind="stock",
                company_id="CIK0000009279",
                company_name="BANK OF MONTREAL",
                source="TSX",
            ),
        ]
    }
    provider = StubProvider(identities)
    store = PortfolioStore(tmp_path / "portfolio")
    store.save(SavedPortfolio(snapshot=sample_snapshot()))

    client = TestClient(create_app(financial=provider, portfolio_store=store))
    resp = client.post("/api/candidates", json={"ticker": "BMO.TO"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["position"] is not None
    assert data["position"]["id"] == "candidate-BMO-XTSE"
    assert data["position"]["listing"] == "XTSE"
    assert data["position"]["currency"] == "CAD"
    assert data["unresolved"] is None


def test_candidate_lookup_not_found(tmp_path):
    provider = StubProvider({})
    store = PortfolioStore(tmp_path / "portfolio")
    store.save(SavedPortfolio(snapshot=sample_snapshot()))

    client = TestClient(create_app(financial=provider, portfolio_store=store))
    resp = client.post("/api/candidates", json={"ticker": "UNKNOWN123"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["position"] is None
    assert data["unresolved"] is not None
    assert data["unresolved"]["reason"] == "not_found"


def test_candidate_lookup_fund_detection(tmp_path):
    identities = {
        "SPY": [
            Identity(
                status="verified",
                ticker="SPY",
                listing="XASE",
                currency="USD",
                kind="etf",
                company_name="SPDR S&P 500 ETF TRUST",
                source="SEC",
            )
        ]
    }
    provider = StubProvider(identities)
    store = PortfolioStore(tmp_path / "portfolio")
    store.save(SavedPortfolio(snapshot=sample_snapshot()))

    client = TestClient(create_app(financial=provider, portfolio_store=store))
    resp = client.post("/api/candidates", json={"ticker": "SPY"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["position"] is not None
    assert data["position"]["id"] == "candidate-SPY-XASE"
    assert data["position"]["kind"] == "etf"
    assert data["is_fund"] is True


def test_candidate_lookup_never_mutates_saved_portfolio(tmp_path):
    identities = {
        "NVDA": [
            Identity(
                status="verified",
                ticker="NVDA",
                listing="XNAS",
                currency="USD",
                kind="stock",
                company_id="CIK0001045810",
                company_name="NVIDIA CORP",
                source="SEC",
            )
        ]
    }
    provider = StubProvider(identities)
    store = PortfolioStore(tmp_path / "portfolio")
    initial_portfolio = SavedPortfolio(snapshot=sample_snapshot())
    store.save(initial_portfolio)
    initial_saved = store.get()

    client = TestClient(create_app(financial=provider, portfolio_store=store))
    resp = client.post("/api/candidates", json={"ticker": "NVDA"})
    assert resp.status_code == 200

    after_saved = store.get()
    assert after_saved == initial_saved
    assert len(after_saved.snapshot.positions) == 1
    assert after_saved.snapshot.positions[0].ticker == "AAPL"
