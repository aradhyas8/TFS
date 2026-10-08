import csv
import io
from datetime import date
from typing import Any

from .schemas import CSVRequest, Snapshot

COLUMNS = [
    "row_type",
    "id",
    "account_id",
    "account_name",
    "ticker",
    "listing",
    "company_id",
    "company_name",
    "shares",
    "cash",
    "currency",
    "mark",
    "mark_date",
    "mark_source",
    "to_currency",
    "fx_rate",
    "fx_date",
    "fx_source",
]


def load_csv(request: CSVRequest) -> Snapshot:
    reader = csv.DictReader(io.StringIO(request.csv.lstrip("\ufeff")), strict=True)
    if reader.fieldnames != COLUMNS:
        raise ValueError("Use the template columns in their supplied order.")
    accounts: dict[str, dict[str, str]] = {}
    positions: list[dict[str, Any]] = []
    rates: list[dict[str, str]] = []
    dates: list[str] = []
    for line, raw in enumerate(reader, start=2):
        if line > 2202 or None in raw or any(value is None for value in raw.values()):
            raise ValueError("CSV has too many rows or a row has the wrong number of columns.")
        row = {key: value.strip() for key, value in raw.items()}
        kind = row["row_type"]
        if kind not in {"account", "stock", "etf", "cash", "fx"}:
            raise ValueError("Every row must be an account, stock, etf, cash or fx.")
        allowed = {"row_type"}
        if kind == "fx":
            allowed.update({"currency", "to_currency", "fx_rate", "fx_date", "fx_source"})
            if row["fx_date"]:
                dates.append(row["fx_date"])
            rates.append(
                {
                    "from_currency": row["currency"],
                    "to_currency": row["to_currency"],
                    "rate": row["fx_rate"],
                    "as_of": row["fx_date"],
                    "source": row["fx_source"],
                }
            )
        else:
            allowed.update({"account_id", "account_name"})
            account_id = row["account_id"]
            account = {"id": account_id, "name": row["account_name"]}
            if not account_id or not account["name"]:
                raise ValueError("Every account/position row needs an account ID and name.")
            if accounts.setdefault(account_id, account) != account:
                raise ValueError("Account names must be consistent for an account ID.")
            if kind != "account":
                allowed.update({"id", "currency"})
                position: dict[str, Any] = {
                    "id": row["id"],
                    "account_id": account_id,
                    "kind": kind,
                    "currency": row["currency"],
                }
                if kind == "cash":
                    allowed.add("cash")
                    position["cash"] = row["cash"]
                else:
                    allowed.update(
                        {
                            "ticker",
                            "listing",
                            "company_id",
                            "company_name",
                            "shares",
                            "mark",
                            "mark_date",
                            "mark_source",
                        }
                    )
                    position["shares"] = row["shares"]
                    for key in ("ticker", "listing", "company_id", "company_name"):
                        if row[key]:
                            position[key] = row[key]
                    if row["mark_date"]:
                        dates.append(row["mark_date"])
                    if any(row[key] for key in ("mark", "mark_date", "mark_source")):
                        position["mark"] = {
                            "value": row["mark"],
                            "as_of": row["mark_date"],
                            "source": row["mark_source"],
                        }
                positions.append(position)
        if any(value and key not in allowed for key, value in row.items()):
            raise ValueError("CSV contains fields that do not belong to its row type.")
    as_of = request.as_of
    if as_of is None:
        if dates:
            try:
                as_of = max(date.fromisoformat(d) for d in dates)
            except ValueError:
                raise ValueError("Dates in CSV mark_date or fx_date must be valid ISO dates (YYYY-MM-DD).")
        else:
            raise ValueError("A snapshot as-of date is required when not present in the CSV.")

    return Snapshot.model_validate(
        {
            "as_of": as_of,
            "reporting_currency": request.reporting_currency,
            "accounts": list(accounts.values()),
            "positions": positions,
            "fx": rates,
        }
    )
