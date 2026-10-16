"""Chat-origin portfolio proposals; Spec 2 owns preparation, preview and confirmation."""

from decimal import Decimal
from typing import Annotated, Any, Literal, cast

from pydantic import Field

from .context import ReadArguments, ReadCapability, RunContext
from .financial_tools import FinancialTools
from .persistence import StaleBaseRevisionError
from .portfolio import SUFFIXES, slug
from .portfolio_actions import compute_differences, prepare_portfolio_action
from .schemas import (
    Identifier,
    PortfolioProposal,
    ProposalClarification,
    SavedPortfolio,
    model_safe,
)

# Signed so change_quantity can carry a stated sale; every other action rejects a negative value.
EditQuantity = Annotated[Decimal, Field(ge=Decimal("-1e12"), le=Decimal("1e12"), max_digits=24, decimal_places=10)]


class PortfolioEdit(ReadArguments):
    action: Literal["set_quantity", "change_quantity", "remove_position", "add_position"]
    position_id: Identifier
    quantity: EditQuantity | None = Field(description=(
        "set_quantity and add_position: the exact quantity held. change_quantity: the signed share change "
        "the user stated, e.g. 5 for bought 5, -3 for sold 3. remove_position: null."))


class PrepareProposalArguments(ReadArguments):
    edits: list[PortfolioEdit] = Field(min_length=1, max_length=100)


def clarify(code: str, field: str, message: str) -> dict[str, Any]:
    clarification = ProposalClarification.model_validate({"code": code, "fields": [field], "message": message})
    return {"clarification_required": clarification.model_dump(mode="json")}


class ProposalTools:
    def __init__(self, financial: FinancialTools) -> None:
        self.financial = financial
        self.capabilities = {
            "prepare_portfolio_proposal": ReadCapability(PrepareProposalArguments,
                "Prepare a preview of factual record edits the user explicitly stated in their own message: "
                "set_quantity records a stated total of shares or a stated cash balance of a bound position; "
                "change_quantity passes only the stated share change and the server adds it to the bound "
                "quantity, so never compute the new total yourself; remove_position takes a null quantity; "
                "add_position takes a resolve_candidate row ID. A purchase or sale records shares only: never "
                "infer prices, costs, fees, taxes or cash movements. For a deposit or withdrawal ask for the "
                "new cash balance. Ask when anything is unstated, including the account when a ticker is held "
                "in more than one. This cannot commit: only the user's confirmation control applies a preview.",
                "bound_current_revision", self.prepare, effect="proposal"),
        }

    def prepare(self, context: RunContext, parsed: PrepareProposalArguments) -> dict[str, Any]:
        saved = context.committed
        if saved is None:
            return clarify("portfolio_required", "portfolio", "Create the portfolio in the holdings form first.")
        positions = {row.id: row.model_dump() for row in saved.snapshot.positions}
        costs = dict(saved.average_costs)
        accounts = {account.id: account.name for account in saved.snapshot.accounts}
        # Previous and proposed quantities, so the answer can state them exactly.
        changes: list[dict[str, Any]] = []

        def note(row: dict[str, Any], previous: Decimal | None, proposed: Decimal | None) -> None:
            changes.append({"position_id": row["id"], "account": accounts.get(row["account_id"], row["account_id"]),
                            "holding": row["ticker"] or row["company_name"] or row["kind"],
                            "field": "cash" if row["kind"] == "cash" else "shares",
                            "previous": None if previous is None else str(previous),
                            "proposed": None if proposed is None else str(proposed)})

        for index, edit in enumerate(parsed.edits):
            target = f"edits.{index}.position_id"
            if any(edit.position_id == earlier.position_id for earlier in parsed.edits[:index]):
                return clarify("invalid_edit", target, "Edit each holding once per preview.")
            if (edit.quantity is None) != (edit.action == "remove_position"):
                return clarify("invalid_edit", f"edits.{index}.quantity",
                               "State the exact quantity to record; a removal takes none.")
            if edit.quantity is not None and edit.quantity < 0 and edit.action != "change_quantity":
                return clarify("invalid_edit", f"edits.{index}.quantity", "A recorded quantity cannot be negative.")
            if edit.action == "add_position":
                found = self.financial.resolved.get(edit.position_id)
                if found is None:
                    return clarify("unknown_position", target,
                                   "Resolve the instrument and its account before adding it.")
                if not edit.quantity:
                    return clarify("invalid_edit", f"edits.{index}.quantity", "State the shares held.")
                if any((row["account_id"], row["ticker"], row["listing"]) ==
                       (found.account_id, found.ticker, found.listing) for row in positions.values()):
                    return clarify("invalid_edit", target, "That listing is already held in the account.")
                # Same ID scheme as imported holdings: the ticker as typed, with its exchange suffix.
                suffix = next((key for key, code in SUFFIXES.items() if code == found.listing), "")
                added = found.model_copy(update={
                    "id": f"{found.account_id}-{slug(f'{found.ticker}{suffix}')}", "shares": edit.quantity})
                if added.id in positions:
                    return clarify("invalid_edit", target, "That listing is already held in the account.")
                positions[added.id] = added.model_dump()
                note(positions[added.id], None, edit.quantity)
            elif edit.position_id not in positions:
                return clarify("unknown_position", target, "Select a position from the saved holdings.")
            elif edit.action == "remove_position":
                row = positions.pop(edit.position_id)
                costs.pop(edit.position_id, None)
                note(row, row["cash" if row["kind"] == "cash" else "shares"], None)
            elif edit.action == "change_quantity":
                row = positions[edit.position_id]
                if row["kind"] == "cash":
                    return clarify("invalid_edit", target,
                                   "Deposits and withdrawals are not recorded as changes: state the new cash balance.")
                if not edit.quantity:
                    return clarify("invalid_edit", f"edits.{index}.quantity", "State a non-zero change in shares.")
                # Python adds the stated change to the bound revision's quantity; the model never does.
                shares = row["shares"] + edit.quantity
                if shares < 0:
                    return clarify("invalid_edit", f"edits.{index}.quantity",
                                   f"That change would leave fewer than zero shares; the saved holdings "
                                   f"record {row['shares']}.")
                note(row, row["shares"], shares or None)
                if shares:
                    row["shares"] = shares
                else:  # every share sold: recorded the same way as remove_position
                    del positions[edit.position_id]
                    costs.pop(edit.position_id, None)
            else:
                row = positions[edit.position_id]
                if row["kind"] != "cash" and not edit.quantity:
                    return clarify("invalid_edit", f"edits.{index}.quantity",
                                   "Zero shares is ambiguous: remove the holding or state the shares held.")
                field = "cash" if row["kind"] == "cash" else "shares"
                note(row, row[field], edit.quantity)
                row[field] = edit.quantity
        # Costs, rules and benchmark are carried forward server-side; the model never supplies them.
        try:
            candidate = SavedPortfolio.model_validate({
                **saved.model_dump(), "average_costs": costs,
                "snapshot": {**saved.snapshot.model_dump(), "positions": list(positions.values())},
            })
        except ValueError:
            return clarify("invalid_edit", "edits", "The edits conflict with the saved holdings.")
        # A repeated call or request reuses the identical pending preview instead of storing another.
        for known in context.known_proposals.values():
            if known.status != "pending" or known.base_revision != context.run.bound_revision:
                continue
            detail = context.db.get_proposal_detail(known.id)
            if (detail and detail.status == "pending" and detail.after_portfolio
                    and not compute_differences(detail.after_portfolio, candidate)):
                return self.output("already_pending", known, changes, context)
        try:
            evidence = getattr(self.financial.provider, "reference", None)
        except (ValueError, OSError):
            evidence = None  # factual recording never depends on source configuration
        try:
            result = prepare_portfolio_action(
                context.db, candidate, context.run.bound_revision, "chat",
                thread_id=context.run.thread_id, run_id=context.run.id, evidence=evidence,
            )
        except StaleBaseRevisionError:
            # Never rebase: this run stays bound to the revision it was accepted with.
            return {"error": "The committed portfolio changed after this run began. "
                             "Send a new message to prepare a fresh preview."}
        proposal = result.proposal
        if proposal is None:
            return {"status": result.status, "proposal": None}
        context.known_proposals[proposal.id] = proposal
        return self.output(result.status, proposal, changes, context)

    @staticmethod
    def output(status: str, proposal: PortfolioProposal, changes: list[dict[str, Any]],
               context: RunContext) -> dict[str, Any]:
        artifact_ids = [ref.id for ref in (proposal.before_artifact, proposal.after_artifact) if ref]
        context.artifact_ids.update(artifact_ids)
        return cast(dict[str, Any], model_safe({
            "status": status, "proposal_ids": [proposal.id], "artifact_ids": artifact_ids,
            "proposal": {
                **proposal.model_dump(mode="json", include={"id", "base_revision", "status", "warnings"}),
                "differences": [d.model_dump(include={"kind", "path", "description"})
                                for d in proposal.differences],
                # Previous and proposed quantities as recorded; None means not held. Nothing is committed yet.
                "changes": changes,
                "committed": False,
            },
        }))
