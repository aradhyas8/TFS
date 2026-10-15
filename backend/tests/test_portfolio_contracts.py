from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from analyst.schemas import (
    Account,
    ArtifactEnvelope,
    ArtifactReference,
    DifferenceItem,
    PortfolioProposal,
    PreparationResult,
    ProposalReceipt,
    RevisionedPortfolio,
    SavedPortfolio,
    Snapshot,
    UnresolvedHolding,
    build_artifact_payload,
)


def sample_portfolio() -> SavedPortfolio:
    return SavedPortfolio(
        snapshot=Snapshot(
            as_of=datetime(2025, 1, 1).date(),
            reporting_currency="CAD",
            accounts=[Account(id="acc-1", name="Tax-Free")],
            positions=[],
            fx=[],
        ),
        average_costs={},
        unresolved=[
            UnresolvedHolding(
                account_id="acc-1",
                ticker="SHOP.TO",
                shares=Decimal("10"),
                average_cost=Decimal("95.50"),
                currency="CAD",
            )
        ],
    )


def test_revisioned_portfolio_requires_positive_revision():
    portfolio = sample_portfolio()
    rev = RevisionedPortfolio(revision=1, portfolio=portfolio, committed_at=datetime.now(UTC))
    assert rev.revision == 1
    assert rev.portfolio.snapshot.reporting_currency == "CAD"

    with pytest.raises(ValidationError):
        RevisionedPortfolio(revision=0, portfolio=portfolio, committed_at=datetime.now(UTC))

    with pytest.raises(ValidationError):
        RevisionedPortfolio(revision=-1, portfolio=portfolio, committed_at=datetime.now(UTC))


def test_artifact_reference_contracts():
    ref = ArtifactReference(
        id="art-123",
        kind="portfolio_snapshot",
        label="Committed Portfolio",
        created_at=datetime.now(UTC),
        revision=1,
        basis="committed",
        source_artifact_id=None,
        originating_run_id=None,
    )
    assert ref.basis == "committed"

    # Invalid basis
    with pytest.raises(ValidationError):
        ArtifactReference(
            id="art-123",
            kind="portfolio_snapshot",
            label="Committed Portfolio",
            created_at=datetime.now(UTC),
            basis="invalid",  # type: ignore
        )


def test_portfolio_proposal_and_receipt_contract():
    before_ref = ArtifactReference(
        id="art-before",
        kind="portfolio_snapshot",
        label="Before",
        created_at=datetime.now(UTC),
        basis="committed",
        revision=1,
    )
    after_ref = ArtifactReference(
        id="art-after",
        kind="portfolio_snapshot",
        label="After",
        created_at=datetime.now(UTC),
        basis="hypothetical",
        source_artifact_id="art-before",
    )

    diff = DifferenceItem(
        kind="position_added",
        path="snapshot.positions[0]",
        description="Added 10 shares of TD",
        before=None,
        after={"ticker": "TD"},
    )

    proposal = PortfolioProposal(
        id="prop-1",
        base_revision=1,
        origin="manual",
        thread_id=None,
        run_id=None,
        before_artifact=before_ref,
        after_artifact=after_ref,
        differences=[diff],
        warnings=["Single stock concentration exceeds limit"],
        status="pending",
        created_at=datetime.now(UTC),
        receipt=None,
    )
    assert proposal.status == "pending"
    assert proposal.receipt is None

    receipt = ProposalReceipt(
        proposal_id="prop-1",
        previous_revision=1,
        new_revision=2,
        committed_at=datetime.now(UTC),
    )
    proposal_applied = proposal.model_copy(update={"status": "applied", "receipt": receipt})
    assert proposal_applied.status == "applied"
    assert proposal_applied.receipt.new_revision == 2


def test_preparation_result_discrimination():
    res_unchanged = PreparationResult(status="unchanged", proposal=None)
    assert res_unchanged.status == "unchanged"
    assert res_unchanged.proposal is None

    proposal = PortfolioProposal(
        id="prop-1",
        base_revision=None,
        origin="import",
        before_artifact=None,
        after_artifact=ArtifactReference(
            id="art-after",
            kind="portfolio_snapshot",
            label="After",
            created_at=datetime.now(UTC),
            basis="hypothetical",
        ),
        differences=[],
        warnings=[],
        status="pending",
        created_at=datetime.now(UTC),
    )
    res_prepared = PreparationResult(status="prepared", proposal=proposal)
    assert res_prepared.status == "prepared"
    assert res_prepared.proposal is not None
    assert res_prepared.proposal.base_revision is None


def test_model_projection_excludes_average_costs():
    portfolio = sample_portfolio()
    # Add an average cost in average_costs dictionary as well
    portfolio = portfolio.model_copy(
        update={
            "average_costs": {"pos-1": Decimal("100.00")},
        }
    )

    human_payload = build_artifact_payload(portfolio, projection="human")
    assert human_payload["average_costs"] == {"pos-1": Decimal("100.00")}
    assert human_payload["unresolved"][0]["average_cost"] == Decimal("95.50")

    model_payload = build_artifact_payload(portfolio, projection="model")
    assert model_payload["average_costs"] == {}
    assert model_payload["unresolved"][0]["average_cost"] is None


def test_extended_artifact_kinds_and_envelopes():
    kinds = [
        "portfolio_snapshot",
        "analysis_result",
        "company_research",
        "financial_evidence",
        "calculation_result",
        "web_research",
    ]
    for kind in kinds:
        ref = ArtifactReference(
            id=f"art-{kind}",
            kind=kind,  # type: ignore[arg-type]
            label=f"Label for {kind}",
            created_at=datetime.now(UTC),
            revision=1 if kind == "portfolio_snapshot" else None,
            basis="committed" if kind == "portfolio_snapshot" else "none",
            source_artifact_id="art-parent" if kind != "portfolio_snapshot" else None,
            originating_run_id="run-1",
        )
        assert ref.kind == kind
        envelope = ArtifactEnvelope(reference=ref, payload={"status": "ok", "kind": kind})
        assert envelope.reference.id == f"art-{kind}"
        assert envelope.payload["kind"] == kind

    # Invalid artifact kind
    with pytest.raises(ValidationError):
        ArtifactReference(
            id="art-invalid",
            kind="unknown_kind",  # type: ignore[arg-type]
            label="Invalid",
            created_at=datetime.now(UTC),
        )
