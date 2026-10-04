"""Dated financial-source boundary. No model supplies identities, prices or rates."""

import os
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Protocol

import httpx

from .schemas import FX, FinancialEvidence, Identity, Position, Quote, Snapshot


class FinancialProvider(Protocol):
    async def identity(self, position: Position, as_of: date) -> Identity: ...
    async def quote(self, position: Position, as_of: date) -> Quote | None: ...
    async def fx(self, from_currency: str, to_currency: str, as_of: date) -> FX | None: ...


class PersonalFinancialProvider:
    """Backend-owned reviewed reference file, broker fallback and optional Valet FX.

    yfinance is intentionally not enabled: library availability does not establish
    coverage or permission to fetch/reuse Yahoo prices for this application.
    """

    def __init__(self, reference: FinancialEvidence | None = None, *, valet: bool = False,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.reference = reference or FinancialEvidence()
        self.valet = valet
        self.transport = transport

    @classmethod
    def from_environment(cls) -> "PersonalFinancialProvider":
        path = os.environ.get("FINANCIAL_REFERENCE_FILE")
        reference = FinancialEvidence.model_validate_json(Path(path).read_text(encoding="utf-8")) if path else None
        return cls(reference, valet=os.environ.get("BOC_FX_ENABLED", "false").lower() == "true")

    async def identity(self, position: Position, as_of: date) -> Identity:
        return self.reference.identities.get(position.id) or Identity(
            status="supplied", ticker=position.ticker, listing=position.listing,
            currency=position.currency, kind=position.kind if position.kind != "cash" else None,
            company_id=position.company_id, company_name=position.company_name,
            source="User-supplied listing identity", as_of=as_of,
        )

    async def quote(self, position: Position, as_of: date) -> Quote | None:
        return self.reference.quotes.get(position.id)

    async def fx(self, from_currency: str, to_currency: str, as_of: date) -> FX | None:
        supplied = next((rate for rate in self.reference.fx if
                         (rate.from_currency, rate.to_currency) == (from_currency, to_currency)), None)
        if supplied or not self.valet or "CAD" not in {from_currency, to_currency}:
            return supplied
        foreign = from_currency if to_currency == "CAD" else to_currency
        series = f"FX{foreign}CAD"
        async with httpx.AsyncClient(transport=self.transport, timeout=10) as client:
            response = await client.get(
                f"https://www.bankofcanada.ca/valet/observations/{series}/json",
                params={"start_date": as_of.isoformat(), "end_date": as_of.isoformat()},
            )
            response.raise_for_status()
            payload = response.json()
            observations = payload.get("observations", []) if isinstance(payload, dict) else []
        if not isinstance(observations, list) or len(observations) != 1 or not isinstance(observations[0], dict) or observations[0].get("d") != as_of.isoformat():
            return None
        observation = observations[0].get(series, {})
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
                  as_of=as_of, source=f"Bank of Canada Valet {series}" +
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
