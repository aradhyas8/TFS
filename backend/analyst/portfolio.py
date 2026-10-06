"""The saved current portfolio: a simple holdings import and one local JSON file."""

import csv
import io
import os
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .csv_input import COLUMNS, load_csv
from .schemas import (
    CANADIAN_LISTINGS,
    US_LISTINGS,
    CSVRequest,
    FinancialEvidence,
    IdentifyHolding,
    Position,
    SavedPortfolio,
    Snapshot,
    UnresolvedHolding,
)

HOLDINGS_COLUMNS = ["account", "ticker", "shares", "average_cost", "currency", "type"]
REQUIRED = {"account", "ticker", "shares"}
# Exchange suffixes people already type for Canadian listings. A bare ticker has no listing of its own.
SUFFIXES = {".TO": "XTSE", ".V": "XTSX", ".NE": "NEOE", ".CN": "XCNQ"}
LISTINGS = US_LISTINGS | CANADIAN_LISTINGS


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def number(text: str, what: str, line: int) -> Decimal:
    try:
        value = Decimal(text.replace(",", "").replace("$", ""))
    except InvalidOperation:
        raise ValueError(f"Line {line}: {what} must be a number.") from None
    if not value.is_finite() or value < 0:
        raise ValueError(f"Line {line}: {what} must be zero or more.")
    return value


def listing_currency(listing: str | None) -> str | None:
    return "USD" if listing in US_LISTINGS else "CAD" if listing in CANADIAN_LISTINGS else None


def split_ticker(entered: str) -> tuple[str, str | None]:
    for suffix, code in SUFFIXES.items():
        if entered.endswith(suffix):
            return entered.removesuffix(suffix), code
    return entered, None


def resolve(holding: UnresolvedHolding, reference: FinancialEvidence | None) -> Position | UnresolvedHolding:
    """A position when listing and type are known from the user, the suffix or the reference identities.

    Nothing is assumed: a bare ticker with no reference identity stays unresolved until the user names its listing.
    """
    base, listing = split_ticker(holding.ticker)
    listing = holding.listing or listing
    identity = next((row for row in (reference.identities.values() if reference else [])
                     if row.ticker == base and row.status in {"verified", "supplied"} and (listing is None or row.listing == listing)), None)
    listing = listing or (identity.listing if identity else None)
    kind = holding.kind or (identity.kind if identity else None)
    currency = holding.currency or (identity.currency if identity else None) or listing_currency(listing)
    if listing is None or kind is None or currency is None:
        return holding.model_copy(update={"listing": listing, "kind": kind, "currency": currency})
    return Position(id=f"{holding.account_id}-{slug(holding.ticker)}", account_id=holding.account_id, kind=kind,
                    currency=currency, ticker=base, listing=listing, shares=holding.shares,
                    company_id=identity.company_id if identity else None, company_name=identity.company_name if identity else None)


def load_holdings(request: CSVRequest, reference: FinancialEvidence | None = None) -> SavedPortfolio:
    """Accepts the simple account/ticker/shares CSV, or the full template CSV unchanged."""
    text = request.csv.lstrip("﻿")
    reader = csv.reader(io.StringIO(text), strict=True)
    header = next(reader, None)
    if header == COLUMNS:
        return SavedPortfolio(snapshot=load_csv(request))
    names = [name.strip().lower().replace(" ", "_") for name in header or []]
    if not REQUIRED <= set(names) or any(name not in HOLDINGS_COLUMNS for name in names) or len(set(names)) != len(names):
        raise ValueError("Use the columns account, ticker, shares, and optionally average_cost, currency and type.")
    accounts: dict[str, dict[str, str]] = {}
    positions: list[Position] = []
    unresolved: list[UnresolvedHolding] = []
    costs: dict[str, Decimal] = {}
    seen: set[tuple[str, str]] = set()
    for line, values in enumerate(reader, start=2):
        if not any(value.strip() for value in values):
            continue
        if line > 2002 or len(values) != len(names):
            raise ValueError(f"Line {line}: wrong number of columns, or too many rows.")
        row = {name: value.strip() for name, value in zip(names, values, strict=True)}
        account, ticker = row["account"], row["ticker"].upper()
        if not account or not ticker or not row["shares"]:
            raise ValueError(f"Line {line}: account, ticker and shares are required.")
        account_id = slug(account)
        if accounts.setdefault(account_id, {"id": account_id, "name": account})["name"] != account:
            raise ValueError(f"Line {line}: account names {account!r} and {accounts[account_id]['name']!r} are too similar; rename one.")
        if (account_id, ticker) in seen:
            raise ValueError(f"Line {line}: {ticker} appears twice in {account}; combine the lots into one row.")
        seen.add((account_id, ticker))
        quantity = number(row["shares"], "shares", line)
        currency = row.get("currency", "").upper() or None
        kind = row.get("type", "").lower() or None
        if kind not in {None, "stock", "etf", "cash"}:
            raise ValueError(f"Line {line}: type must be stock, etf or cash.")
        if kind == "cash" or (ticker == "CASH" and kind is None):
            # Optional real cash balance; `shares` holds the amount.
            if row.get("average_cost"):
                raise ValueError(f"Line {line}: cash has no average cost.")
            currency = currency or request.reporting_currency
            positions.append(Position(id=f"{account_id}-cash-{currency.lower()}", account_id=account_id,
                                      kind="cash", currency=currency, cash=quantity))
            continue
        cost = number(row["average_cost"], "average_cost", line) if row.get("average_cost") else None
        found = resolve(UnresolvedHolding(account_id=account_id, ticker=ticker, shares=quantity, average_cost=cost,
                                          currency=currency, kind=kind), reference)  # type: ignore[arg-type]
        if isinstance(found, UnresolvedHolding):
            unresolved.append(found)
            continue
        positions.append(found)
        if cost is not None:
            costs[found.id] = cost
    if not positions and not unresolved:
        raise ValueError("The CSV has no holdings.")
    snapshot = Snapshot.model_validate({"as_of": request.as_of or date.today(), "reporting_currency": request.reporting_currency,
                                        "accounts": list(accounts.values()), "positions": [row.model_dump() for row in positions]})
    return SavedPortfolio(snapshot=snapshot, average_costs=costs, unresolved=unresolved)


def identify(saved: SavedPortfolio, answer: IdentifyHolding, reference: FinancialEvidence | None) -> SavedPortfolio:
    """Applies the user's listing/type answer to one unresolved holding; it moves into the snapshot once complete."""
    holding = next((row for row in saved.unresolved if (row.account_id, row.ticker) == (answer.account_id, answer.ticker)), None)
    if holding is None:
        raise ValueError("That holding is not waiting for identification.")
    if answer.listing is not None and answer.listing not in LISTINGS:
        raise ValueError("Choose a supported US or Canadian exchange.")
    found = resolve(holding.model_copy(update={"listing": answer.listing or holding.listing, "kind": answer.kind or holding.kind}), reference)
    rest = [row for row in saved.unresolved if row is not holding]
    if isinstance(found, UnresolvedHolding):
        return saved.model_copy(update={"unresolved": [*rest, found]})
    if any(row.id == found.id for row in saved.snapshot.positions):
        raise ValueError(f"{holding.ticker} is already held in that account.")
    snapshot = Snapshot.model_validate({**saved.snapshot.model_dump(), "positions": [*saved.snapshot.model_dump()["positions"], found.model_dump()]})
    costs = {**saved.average_costs, **({found.id: holding.average_cost} if holding.average_cost is not None else {})}
    return saved.model_copy(update={"snapshot": snapshot, "average_costs": costs, "unresolved": rest})


class PortfolioStore:
    """One current portfolio in a local JSON file, like the decision store."""

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory or os.environ.get("PORTFOLIO_DIR") or Path(__file__).resolve().parents[1] / "data" / "portfolio")
        self.path = self.directory / "current.json"

    def get(self) -> SavedPortfolio | None:
        if not self.path.exists():
            return None
        return SavedPortfolio.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, record: SavedPortfolio) -> SavedPortfolio:
        record = SavedPortfolio.model_validate({**record.model_dump(), "saved_at": datetime.now(UTC)})
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self.path)
        return record

