from decimal import Decimal

from analyst.calculations import review_portfolio
from analyst.guardrails import apply_guardrails
from analyst.providers import DataProvider, ModelTurn
from analyst.schemas import (
    FinancialEvidence,
    PortfolioSettings,
    Snapshot,
)


class ScriptedModel:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.requests: list[dict] = []

    async def respond(self, messages: list[dict], *, require_tool: bool) -> ModelTurn:
        self.requests.append(messages)
        if not self.turns:
            raise AssertionError("Model called more times than expected.")
        return self.turns.pop(0)


class FakeData(DataProvider):
    def snapshot(self, data: Snapshot) -> Snapshot:
        return data


def make_portfolio_snapshot(as_of: str = "2026-09-30") -> Snapshot:
    return Snapshot.model_validate({
        "as_of": as_of,
        "reporting_currency": "USD",
        "accounts": [{"id": "acc1", "name": "Brokerage"}],
        "positions": [
            {
                "id": "p_acme",
                "account_id": "acc1",
                "kind": "stock",
                "currency": "USD",
                "ticker": "ACME",
                "listing": "XNAS",
                "company_id": "acme",
                "company_name": "Acme Corp",
                "shares": "10",
                "mark": {"value": "100", "as_of": as_of, "source": "Manual"},
            },
            {
                "id": "p_etf",
                "account_id": "acc1",
                "kind": "etf",
                "currency": "USD",
                "ticker": "BROAD",
                "listing": "XNYS",
                "shares": "20",
                "etf_role": "diversified",
                "mark": {"value": "100", "as_of": as_of, "source": "Manual"},
            },
            {
                "id": "p_cash",
                "account_id": "acc1",
                "kind": "cash",
                "currency": "USD",
                "cash": "1000",
            },
        ],
    })


def make_evidence_fixture(as_of: str = "2026-09-30", sponsor_holdings: dict | None = None) -> FinancialEvidence:
    return FinancialEvidence.model_validate({
        "identities": {
            "p_acme": {
                "status": "verified",
                "ticker": "ACME",
                "listing": "XNAS",
                "currency": "USD",
                "kind": "stock",
                "company_id": "acme",
                "company_name": "Acme Corp",
                "source": "Exchange",
                "source_url": "https://example.test/acme",
                "as_of": as_of,
                "captured_at": f"{as_of}T20:00:00Z",
            },
            "p_etf": {
                "status": "verified",
                "ticker": "BROAD",
                "listing": "XNYS",
                "currency": "USD",
                "kind": "etf",
                "source": "Exchange",
                "source_url": "https://example.test/broad",
                "as_of": as_of,
                "captured_at": f"{as_of}T20:00:00Z",
            },
        },
        "quotes": {
            "p_acme": {
                "value": "100",
                "ticker": "ACME",
                "listing": "XNAS",
                "currency": "USD",
                "as_of": as_of,
                "source": "Provider",
                "status": "delayed",
                "basis": "unadjusted",
                "captured_at": f"{as_of}T20:00:00Z",
            },
            "p_etf": {
                "value": "100",
                "ticker": "BROAD",
                "listing": "XNYS",
                "currency": "USD",
                "as_of": as_of,
                "source": "Provider",
                "status": "delayed",
                "basis": "unadjusted",
                "captured_at": f"{as_of}T20:00:00Z",
            },
        },
        "sponsor_holdings": sponsor_holdings or {},
    })


def test_full_sponsor_holdings_direct_and_indirect_aggregation():
    snapshot = make_portfolio_snapshot()
    evidence = make_evidence_fixture(
        sponsor_holdings={
            "p_etf": {
                "as_of": "2026-09-30",
                "source": "Sponsor Daily Holdings",
                "source_url": "https://example.test/holdings/broad",
                "coverage": "full",
                "holdings": [
                    {
                        "company_id": "acme",
                        "company_name": "Acme Corp",
                        "ticker": "ACME",
                        "listing": "XNAS",
                        "kind": "stock",
                        "weight": "0.25",
                    },
                    {
                        "company_id": "widget",
                        "company_name": "Widget Co",
                        "ticker": "WDGT",
                        "listing": "XNAS",
                        "kind": "stock",
                        "weight": "0.75",
                    },
                ],
            }
        }
    )

    review = review_portfolio(snapshot, evidence)
    assert review.total_value == "4000"
    assert review.indirect_exposure == "full"

    # Check direct companies (stock only) preserved
    assert len(review.direct_companies) == 1
    assert review.direct_companies[0].company_id == "acme"
    assert review.direct_companies[0].value == "1000"

    # Check company overlap
    overlap = {row.company_id: row for row in review.company_overlap}
    assert "acme" in overlap
    assert "widget" in overlap

    acme = overlap["acme"]
    assert acme.direct_value == "1000"
    assert acme.indirect_value == "500"  # 25% of 2000
    assert acme.total_value == "1500"
    assert acme.direct_weight == "0.25000000"
    assert acme.indirect_weight == "0.12500000"
    assert acme.total_weight == "0.37500000"
    assert acme.coverage == "full"
    assert len(acme.contributing_funds) == 1
    assert acme.contributing_funds[0].position_id == "p_etf"
    assert acme.contributing_funds[0].weight_in_fund == "0.25"
    assert acme.contributing_funds[0].indirect_value == "500"
    assert acme.contributing_funds[0].source == "Sponsor Daily Holdings"

    widget = overlap["widget"]
    assert widget.direct_value is None or widget.direct_value == "0"
    assert widget.indirect_value == "1500"
    assert widget.total_value == "1500"
    assert widget.indirect_weight == "0.37500000"
    assert widget.total_weight == "0.37500000"
    assert widget.coverage == "full"


def test_nested_fund_look_through():
    snapshot = make_portfolio_snapshot()
    evidence = make_evidence_fixture(
        sponsor_holdings={
            "p_etf": {
                "as_of": "2026-09-30",
                "source": "Parent Fund Sponsor",
                "coverage": "full",
                "holdings": [
                    {
                        "company_id": "acme",
                        "company_name": "Acme Corp",
                        "ticker": "ACME",
                        "listing": "XNAS",
                        "kind": "stock",
                        "weight": "0.60",
                    },
                    {
                        "ticker": "SUB_ETF",
                        "listing": "XNYS",
                        "kind": "etf",
                        "weight": "0.40",
                    },
                ],
            },
            "SUB_ETF": {
                "as_of": "2026-09-30",
                "source": "Sub Fund Sponsor",
                "coverage": "full",
                "holdings": [
                    {
                        "company_id": "acme",
                        "company_name": "Acme Corp",
                        "ticker": "ACME",
                        "listing": "XNAS",
                        "kind": "stock",
                        "weight": "0.50",
                    },
                    {
                        "company_id": "zenith",
                        "company_name": "Zenith Ltd",
                        "ticker": "ZNTH",
                        "listing": "XNAS",
                        "kind": "stock",
                        "weight": "0.50",
                    },
                ],
            },
        }
    )

    review = review_portfolio(snapshot, evidence)
    assert review.indirect_exposure == "full"
    overlap = {row.company_id: row for row in review.company_overlap}
    assert "acme" in overlap
    assert "zenith" in overlap

    # Effective Acme in p_etf: 0.60 + 0.40 * 0.50 = 0.80 (80% of 2000 = 1600)
    # Plus direct Acme: 1000 -> total = 2600
    acme = overlap["acme"]
    assert acme.indirect_value == "1600"
    assert acme.total_value == "2600"
    assert acme.total_weight == "0.65000000"

    # Effective Zenith in p_etf: 0.40 * 0.50 = 0.20 (20% of 2000 = 400)
    zenith = overlap["zenith"]
    assert zenith.indirect_value == "400"
    assert zenith.total_value == "400"
    assert zenith.total_weight == "0.10000000"


def test_partial_sponsor_holdings_labeled_partial_not_zero():
    snapshot = make_portfolio_snapshot()
    evidence = make_evidence_fixture(
        sponsor_holdings={
            "p_etf": {
                "as_of": "2026-09-30",
                "source": "SEC Form N-PORT Quarterly Excerpt",
                "coverage": "partial",
                "holdings": [
                    {
                        "company_id": "acme",
                        "company_name": "Acme Corp",
                        "ticker": "ACME",
                        "listing": "XNAS",
                        "kind": "stock",
                        "weight": "0.10",
                    }
                ],
            }
        }
    )

    review = review_portfolio(snapshot, evidence)
    assert review.indirect_exposure == "partial"
    overlap = {row.company_id: row for row in review.company_overlap}
    assert "acme" in overlap
    assert overlap["acme"].coverage == "partial"
    assert overlap["acme"].indirect_value == "200"

    # Must contain qualification that look-through is partial
    assert any("partial" in q.lower() for q in review.qualifications)


def test_stale_sponsor_holdings_labeled_stale():
    snapshot = make_portfolio_snapshot()
    evidence = make_evidence_fixture(
        sponsor_holdings={
            "p_etf": {
                "as_of": "2026-06-30",  # Stale: earlier than snapshot date 2026-09-30
                "source": "Outdated Sponsor Report",
                "coverage": "full",
                "holdings": [
                    {
                        "company_id": "acme",
                        "company_name": "Acme Corp",
                        "ticker": "ACME",
                        "listing": "XNAS",
                        "kind": "stock",
                        "weight": "0.20",
                    }
                ],
            }
        }
    )

    review = review_portfolio(snapshot, evidence)
    assert review.indirect_exposure == "stale"
    assert any("stale" in q.lower() for q in review.qualifications)


def test_missing_sponsor_holdings_labeled_unknown():
    snapshot = make_portfolio_snapshot()
    evidence = make_evidence_fixture(sponsor_holdings={})

    review = review_portfolio(snapshot, evidence)
    assert review.indirect_exposure == "unknown"


def test_indirect_cap_policy_direct_only_vs_include_known_indirect():
    snapshot = make_portfolio_snapshot()
    # Total portfolio is 4000.
    # Acme direct: 1000 (25%).
    # Acme indirect from ETF: 500 (12.5%).
    # Acme total: 1500 (37.5%).
    # Cap is 30% (1200).
    evidence = make_evidence_fixture(
        sponsor_holdings={
            "p_etf": {
                "as_of": "2026-09-30",
                "source": "Sponsor Daily Holdings",
                "coverage": "full",
                "holdings": [
                    {
                        "company_id": "acme",
                        "company_name": "Acme Corp",
                        "ticker": "ACME",
                        "listing": "XNAS",
                        "kind": "stock",
                        "weight": "0.25",
                    }
                ],
            }
        }
    )

    # 1. direct_only: 1000 <= 1200 -> within_limit!
    review_direct = review_portfolio(snapshot, evidence)
    apply_guardrails(
        review_direct,
        PortfolioSettings(single_company_cap=Decimal("0.30"), indirect_cap_policy="direct_only"),
    )
    guardrail_direct = review_direct.guardrails
    assert guardrail_direct is not None
    acme_check_direct = next(c for c in guardrail_direct.companies if c.company_id == "acme")
    assert acme_check_direct.status == "within_limit"
    assert acme_check_direct.current_weight == "0.25000000"
    assert acme_check_direct.excess_value == "0"

    # 2. include_known_indirect: 1500 > 1200 -> breached!
    review_indirect = review_portfolio(snapshot, evidence)
    apply_guardrails(
        review_indirect,
        PortfolioSettings(single_company_cap=Decimal("0.30"), indirect_cap_policy="include_known_indirect"),
    )
    guardrail_indirect = review_indirect.guardrails
    assert guardrail_indirect is not None
    acme_check_indirect = next(c for c in guardrail_indirect.companies if c.company_id == "acme")
    assert acme_check_indirect.status == "breached"
    assert acme_check_indirect.current_weight == "0.37500000"
    assert acme_check_indirect.excess_value == "300"
    assert acme_check_indirect.reduction_to_cash == "300"


def test_partial_look_through_qualifies_but_does_not_block_within_limits():
    snapshot = make_portfolio_snapshot()
    # Total portfolio is 4000.
    # Acme direct: 1000 (25%).
    # Acme indirect from ETF: 200 (5%).
    # Total Acme: 1200 (30%).
    # Cap is 35% (1400).
    evidence = make_evidence_fixture(
        sponsor_holdings={
            "p_etf": {
                "as_of": "2026-09-30",
                "source": "Top 10 Holdings",
                "coverage": "partial",
                "holdings": [
                    {
                        "company_id": "acme",
                        "company_name": "Acme Corp",
                        "ticker": "ACME",
                        "listing": "XNAS",
                        "kind": "stock",
                        "weight": "0.10",
                    }
                ],
            }
        }
    )

    review = review_portfolio(snapshot, evidence)
    apply_guardrails(
        review,
        PortfolioSettings(
            single_company_cap=Decimal("0.35"),
            active_budget=Decimal("0.90"),
            indirect_cap_policy="include_known_indirect",
        ),
    )
    guardrail = review.guardrails
    assert guardrail is not None
    acme_check = next(c for c in guardrail.companies if c.company_id == "acme")
    # Known indirect exposure (30%) is within the cap (35%), qualified by partial coverage
    assert acme_check.status == "within_limit"
    assert acme_check.current_weight == "0.30000000"
    assert "partial" in acme_check.explanation.lower() or "incomplete" in acme_check.explanation.lower()


def test_allocation_sizing_respects_indirect_cap_policy_and_partial_coverage():
    from types import SimpleNamespace

    from analyst.allocation import size_allocation
    from analyst.schemas import AllocationJudgment, NewCashInput

    snapshot = make_portfolio_snapshot()
    evidence = make_evidence_fixture(
        sponsor_holdings={
            "p_etf": {
                "as_of": "2026-09-30",
                "source": "Top 10 Holdings",
                "coverage": "partial",
                "holdings": [
                    {
                        "company_id": "acme",
                        "company_name": "Acme Corp",
                        "ticker": "ACME",
                        "listing": "XNAS",
                        "kind": "stock",
                        "weight": "0.10",
                    }
                ],
            }
        }
    )
    current = review_portfolio(snapshot, evidence)
    context = NewCashInput(
        amount=Decimal("2000"),
        cash_position_id="p_cash",
        confirmed=True,
        risk_context="Long horizon.",
    )
    judgment = AllocationJudgment(
        position_id="p_acme",
        min_weight=Decimal("0.30"),
        max_weight=Decimal("0.30"),
        reason="Target 30% exposure.",
    )
    dummy_case = SimpleNamespace(terminal_price="120")
    dummy_stock = SimpleNamespace(position_id="p_acme", cases=[dummy_case])

    # 1. include_known_indirect:
    # Portfolio total = 4000. New cash = 2000. Post total = 6000.
    # Desired weight = 0.30 -> desired total value = 1800.
    # Existing direct = 1000, existing indirect = 200 (10% of 2000). Total existing = 1200.
    # Required addition = 1800 - 1200 = 600.
    # Partial coverage qualifies but does not block sizing when within cap (40%).
    settings_indirect = PortfolioSettings(
        single_company_cap=Decimal("0.40"),
        active_budget=Decimal("0.90"),
        indirect_cap_policy="include_known_indirect",
        cash_is_deliberate_tilt=False,
    )
    alloc_indirect = SimpleNamespace(context=context, stocks=[dummy_stock], previews=[], missing_inputs=[], amount=None, judgment=None)
    req_indirect_alloc = SimpleNamespace(settings=settings_indirect)
    size_allocation(req_indirect_alloc, snapshot, evidence, current, alloc_indirect, judgment)
    assert alloc_indirect.amount is not None
    assert alloc_indirect.amount.minimum == "600"
    assert alloc_indirect.amount.maximum == "600"
    assert alloc_indirect.previews[0].status == "within_limits"

    # 2. direct_only:
    # Existing considered = 1000 (direct only).
    # Required addition = 1800 - 1000 = 800.
    settings_direct = PortfolioSettings(
        single_company_cap=Decimal("0.40"),
        active_budget=Decimal("0.90"),
        indirect_cap_policy="direct_only",
        cash_is_deliberate_tilt=False,
    )
    alloc_direct = SimpleNamespace(context=context, stocks=[dummy_stock], previews=[], missing_inputs=[], amount=None, judgment=None)
    req_direct_alloc = SimpleNamespace(settings=settings_direct)
    size_allocation(req_direct_alloc, snapshot, evidence, current, alloc_direct, judgment)
    assert alloc_direct.amount is not None
    assert alloc_direct.amount.minimum == "800"
    assert alloc_direct.amount.maximum == "800"
    assert alloc_direct.previews[0].status == "within_limits"


def test_review_sizing_respects_indirect_cap_policy():
    from types import SimpleNamespace

    from analyst.reunderwriting import size_review
    from analyst.schemas import (
        PortfolioReviewInput,
        ReunderwritingResult,
        ReviewSizingInput,
        ThesisAssessment,
    )

    snapshot = make_portfolio_snapshot()
    evidence = make_evidence_fixture(
        sponsor_holdings={
            "p_etf": {
                "as_of": "2026-09-30",
                "source": "Full holdings",
                "coverage": "full",
                "holdings": [
                    {
                        "company_id": "acme",
                        "company_name": "Acme Corp",
                        "ticker": "ACME",
                        "listing": "XNAS",
                        "kind": "stock",
                        "weight": "0.10",
                    }
                ],
            }
        }
    )
    current = review_portfolio(snapshot, evidence)
    # Total portfolio = 4000. Acme direct = 1000. Acme indirect = 200. Total Acme = 1200.
    # Desired weight: 0.15 (15% of 4000 = 600).
    # Direction: reduce.
    assessment = ThesisAssessment(
        position_id="p_acme",
        status="changed",
        action="reduce",
        current_thesis="Weaker demand warrants reducing exposure.",
        change_reason="Demand weakness.",
        downside="Operating impairment.",
        what_could_change=["Demand improvement."],
        evidence_ids=[],
    )
    alt = SimpleNamespace(id="alt1", position_id="p_acme", kind="stock")
    effect = SimpleNamespace(alternative_id="alt1", as_of=snapshot.as_of, transaction_cost=Decimal(0), terminal_tax=Decimal(0))
    comparison = SimpleNamespace(alternatives=[alt], effects=[effect])
    judgment = ReviewSizingInput(
        position_id="p_acme",
        cash_position_id="p_cash",
        min_weight=Decimal("0.15"),
        max_weight=Decimal("0.15"),
        reason="Reduce to 15% total exposure.",
    )

    # 1. include_known_indirect:
    # Target = 600. Existing total = 1200. Delta = -600. Amount = 600.
    req_indirect = SimpleNamespace(
        comparison=comparison,
        settings=PortfolioSettings(
            single_company_cap=Decimal("0.40"),
            active_budget=Decimal("0.90"),
            indirect_cap_policy="include_known_indirect",
            cash_is_deliberate_tilt=False,
        ),
    )
    result_indirect = ReunderwritingResult(
        context=PortfolioReviewInput(risk_context="Long horizon."),
        assessments=[assessment],
    )
    size_review(req_indirect, snapshot, evidence, current, result_indirect, judgment)
    assert result_indirect.amount is not None
    assert result_indirect.amount.minimum == "600"
    assert result_indirect.amount.maximum == "600"

    # 2. direct_only:
    # Target = 600. Existing direct = 1000. Delta = -400. Amount = 400.
    req_direct = SimpleNamespace(
        comparison=comparison,
        settings=PortfolioSettings(
            single_company_cap=Decimal("0.40"),
            active_budget=Decimal("0.90"),
            indirect_cap_policy="direct_only",
            cash_is_deliberate_tilt=False,
        ),
    )
    result_direct = ReunderwritingResult(
        context=PortfolioReviewInput(risk_context="Long horizon."),
        assessments=[assessment],
    )
    size_review(req_direct, snapshot, evidence, current, result_direct, judgment)
    assert result_direct.amount is not None
    assert result_direct.amount.minimum == "400"
    assert result_direct.amount.maximum == "400"


