import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.providers import FakeDataProvider, ModelTurn, ScriptedModel, ToolCall
from tests.test_analysis import recommendation, snapshot


def review(portfolio=None, settings=None, proposed_changes=None, model_proposal=None, answer=None):
    turns = [ModelTurn(calls=[ToolCall("review", "review_portfolio", "{}")])]
    if model_proposal is not None:
        import json

        turns.append(
            ModelTurn(
                calls=[ToolCall("preview", "check_proposed_changes", json.dumps(model_proposal))]
            )
        )
    turns.append(ModelTurn(answer=answer or recommendation()))
    payload = {
        "question": "Check my explicit portfolio limits",
        "portfolio": portfolio or snapshot(),
    }
    if settings is not None:
        payload["settings"] = settings
    if proposed_changes is not None:
        payload["proposed_changes"] = proposed_changes
    return TestClient(create_app(model=ScriptedModel(turns), data=FakeDataProvider())).post(
        "/api/analyze", json=payload
    )


def test_explicit_cap_aggregates_accounts_and_explains_reduction_to_cash():
    response = review(settings={"single_company_cap": "0.5"})
    assert response.status_code == 200, response.text
    result = response.json()
    guardrails = result["portfolio"]["guardrails"]
    assert guardrails["settings"]["single_company_cap"] == "0.5"
    assert guardrails["settings"]["active_budget"] is None
    company = guardrails["companies"][0]
    assert company["current_weight"] == "0.63888889"
    assert company["status"] == "breached"
    assert company["excess_value"] == "500"
    assert company["reduction_to_cash"] == "500"
    assert "exception" in company["explanation"]
    assert result["portfolio"]["baseline"] is None
    assert result["recommendation"]["amount"] is None


def test_proposed_new_cash_and_cross_account_purchase_cannot_waive_cap():
    changes = {
        "new_cash": [{"cash_position_id": "c1", "amount": "1000"}],
        "trades": [{"position_id": "p2", "shares_change": "5", "cash_position_id": "c1"}],
    }
    # A trade must be funded in its own account; use broker CAD cash for this preview.
    portfolio = snapshot()
    portfolio["positions"][2]["account_id"] = "broker"
    response = review(
        portfolio,
        {"single_company_cap": "0.6", "active_budget": "0.9", "cash_is_deliberate_tilt": False},
        changes,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    preview = result["proposals"][0]
    assert preview["post_total_value"] == "4600"
    assert preview["post_cash_value"] == "1300"
    assert preview["guardrails"]["companies"][0]["current_weight"] == "0.71739130"
    assert preview["guardrails"]["companies"][0]["excess_value"] == "540"
    assert preview["status"] == "blocked"
    assert result["recommendation"]["preferred_action"] == "wait_for_inputs"
    assert result["portfolio"]["total_value"] == "3600"


@pytest.mark.parametrize("model_proposal", [False, True])
def test_reducing_an_above_cap_position_has_a_forward_path_without_approving_exception(
    model_proposal,
):
    changes = {
        "new_cash": [],
        "trades": [{"position_id": "p2", "shares_change": "-2.5", "cash_position_id": "c2"}],
    }
    portfolio = snapshot()
    portfolio["positions"][3].update(currency="CAD", cash="650")
    response = review(
        portfolio,
        {"single_company_cap": "0.5", "active_budget": "0.8", "cash_is_deliberate_tilt": False},
        None if model_proposal else changes,
        changes if model_proposal else None,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["portfolio"]["guardrails"]["companies"][0]["status"] == "breached"
    preview = result["proposals"][0]
    assert preview["post_total_value"] == "3600"
    assert preview["post_cash_value"] == "1800"
    assert preview["guardrails"]["companies"][0]["current_weight"] == "0.50000000"
    assert preview["status"] == "within_limits"
    assert result["recommendation"]["amount"] is None


def classified_portfolio():
    portfolio = snapshot()
    for position in portfolio["positions"]:
        if position["kind"] == "stock":
            position.update(kind="etf", company_id=None, company_name=None)
    portfolio["positions"][0]["etf_role"] = "diversified"
    portfolio["positions"][1]["etf_role"] = "sector_theme"
    return portfolio


def test_active_budget_counts_sector_etfs_and_explicit_excess_cash_against_partial_baseline():
    response = review(
        classified_portfolio(),
        {"active_budget": "0.5", "baseline": {"cash": "0.1"}, "cash_is_deliberate_tilt": True},
    )
    assert response.status_code == 200, response.text
    result = response.json()["portfolio"]
    active = result["guardrails"]["active"]
    assert active["contributions"] == {
        "stocks": "0",
        "sector_theme_etfs": "1000",
        "excess_cash": "940",
    }
    assert active["value"] == "1940"
    assert active["weight"] == "0.53888889"
    assert active["status"] == "breached"
    baseline = {row["category"]: row for row in result["guardrails"]["baseline_comparison"]}
    assert baseline["cash"]["difference"] == "0.26111111"
    assert baseline["stocks"]["baseline_weight"] is None
    assert baseline["stocks"]["difference"] is None
    assert result["baseline"]["cash"] == "0.1"


@pytest.mark.parametrize("missing", ["classification", "cash_tilt", "cash_baseline"])
def test_missing_active_inputs_cannot_clear_budget(missing):
    portfolio = classified_portfolio()
    settings = {
        "active_budget": "0.9",
        "baseline": {"cash": "0.1"},
        "cash_is_deliberate_tilt": True,
    }
    if missing == "classification":
        portfolio["positions"][1].pop("etf_role")
    elif missing == "cash_tilt":
        settings.pop("cash_is_deliberate_tilt")
    else:
        settings.pop("baseline")
    response = review(portfolio, settings)
    assert response.status_code == 200, response.text
    active = response.json()["portfolio"]["guardrails"]["active"]
    assert active["status"] == "unknown"
    assert active["weight"] is None
    assert active["value"] is None
    assert active["qualifications"]


@pytest.mark.parametrize(
    "policy,expected",
    [(None, "unknown"), ("include_known_indirect", "unknown"), ("direct_only", "within_limit")],
)
def test_unknown_etf_overlap_preserves_explicit_cap_policy(policy, expected):
    portfolio = snapshot()
    portfolio["positions"][0].update(
        kind="etf", company_id=None, company_name=None, etf_role="diversified"
    )
    settings = {"single_company_cap": "0.5", "indirect_cap_policy": policy}
    response = review(portfolio, settings)
    assert response.status_code == 200, response.text
    guardrails = response.json()["portfolio"]["guardrails"]
    assert guardrails["companies"][0]["current_weight"] == "0.27777778"
    assert guardrails["companies"][0]["status"] == expected
    assert guardrails["settings"]["indirect_cap_policy"] == policy
    assert response.json()["portfolio"]["indirect_exposure"] == "unknown"


@pytest.mark.parametrize("missing", ["mark", "fx", "stale_mark", "stale_fx", "identity"])
def test_unusable_sources_prevent_cap_and_proposal_conclusions(missing):
    portfolio = snapshot()
    if missing == "mark":
        portfolio["positions"][0]["mark"] = None
    elif missing == "fx":
        portfolio["fx"] = []
    elif missing == "stale_mark":
        portfolio["positions"][0]["mark"]["as_of"] = "2026-09-29"
    elif missing == "stale_fx":
        portfolio["fx"][0]["as_of"] = "2026-09-29"
    else:
        portfolio["positions"][0]["company_id"] = None
    response = review(
        portfolio,
        {"single_company_cap": "0.5", "active_budget": "0.8", "cash_is_deliberate_tilt": False},
        {"new_cash": [{"cash_position_id": "c1", "amount": "1000"}], "trades": []},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["portfolio"]["guardrails"]["companies"][0]["status"] == "unknown"
    assert result["proposals"][0]["status"] == "unknown"
    assert result["proposals"][0]["post_total_value"] is None


@pytest.mark.parametrize(
    "settings", [None, {}, {"baseline": {"cash": "0.1"}}, {"single_company_cap": "0"}]
)
def test_absent_settings_remain_unknown_without_invented_targets_or_risk_scores(settings):
    response = review(settings=settings)
    assert response.status_code == 200, response.text
    result = response.json()["portfolio"]
    if settings is None:
        assert result["guardrails"] is None
    else:
        stored = result["guardrails"]["settings"]
        assert stored["single_company_cap"] == settings.get("single_company_cap")
        assert stored["active_budget"] is None
    if not settings or not settings.get("baseline"):
        assert result["baseline"] is None
    assert "risk_score" not in result
    assert result["sizing_eligible"] is False


@pytest.mark.parametrize(
    "settings",
    [
        {"single_company_cap": "1.01"},
        {"active_budget": "-0.1"},
        {"baseline": {"cash": "0.6", "stocks": "0.5"}},
        {"indirect_cap_policy": "ignore_missing"},
        {"risk_score": 5},
    ],
)
def test_invalid_personal_settings_are_rejected(settings):
    response = review(settings=settings)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        {"position_id": "p2", "shares_change": "100", "cash_position_id": "c1"},
        {"position_id": "p2", "shares_change": "-6", "cash_position_id": "c1"},
        {"position_id": "unknown", "shares_change": "1", "cash_position_id": "c1"},
    ],
)
def test_invalid_or_unfunded_proposal_cannot_be_approved(change):
    portfolio = snapshot()
    portfolio["positions"][2]["account_id"] = "broker"
    response = review(
        portfolio,
        {"single_company_cap": "1", "active_budget": "1", "cash_is_deliberate_tilt": False},
        {"new_cash": [], "trades": [change]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["proposals"][0]["status"] == "blocked"
    assert response.json()["proposals"][0]["post_total_value"] is None


@pytest.mark.parametrize(
    "cap,budget,blocked_check", [("0.7", "1", "company"), ("1", "0.7", "active")]
)
def test_fake_model_proposal_breaches_cannot_be_cleared_by_conviction(cap, budget, blocked_check):
    portfolio = snapshot()
    portfolio["positions"][3].update(currency="CAD", cash="650")
    changes = {
        "new_cash": [],
        "trades": [{"position_id": "p2", "shares_change": "2", "cash_position_id": "c2"}],
    }
    answer = recommendation()
    answer.update(
        preferred_action="no_action",
        reason="Strong conviction supports ignoring the configured limits.",
        alternatives=[
            {
                "action": "keep_snapshot",
                "reason": "Conviction permits an exception to personal limits.",
            }
        ],
    )
    response = review(
        portfolio,
        {"single_company_cap": cap, "active_budget": budget, "cash_is_deliberate_tilt": False},
        model_proposal=changes,
        answer=answer,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    preview = result["proposals"][0]
    assert preview["source"] == "model"
    assert preview["post_total_value"] == "3600"
    assert preview["status"] == "blocked"
    check = (
        preview["guardrails"]["companies"][0]
        if blocked_check == "company"
        else preview["guardrails"]["active"]
    )
    assert check["status"] == "breached"
    assert result["recommendation"]["preferred_action"] == "wait_for_inputs"
    assert "cannot be cleared" in result["recommendation"]["reason"]
    assert result["recommendation"]["alternatives"][0]["action"] == "clarify_inputs"


def test_model_cannot_invent_new_cash_to_dilute_company_weight():
    response = review(
        settings={
            "single_company_cap": "0.5",
            "active_budget": "0.8",
            "cash_is_deliberate_tilt": False,
        },
        model_proposal={"new_cash": [{"cash_position_id": "c1", "amount": "100000"}], "trades": []},
    )
    assert response.status_code == 502
    assert "recommendation" not in response.json()


def test_barely_breached_cap_is_checked_before_display_rounding():
    response = review(settings={"single_company_cap": "0.6388888888"})
    assert response.status_code == 200, response.text
    company = response.json()["portfolio"]["guardrails"]["companies"][0]
    assert company["status"] == "breached"
    assert company["excess_value"] == "0.00000032"


def test_known_active_lower_bound_can_breach_with_unclassified_etf_and_unknown_cash():
    portfolio = snapshot()
    portfolio["positions"][1].update(kind="etf", company_id=None, company_name=None)
    response = review(portfolio, {"active_budget": "0.3"})
    assert response.status_code == 200, response.text
    active = response.json()["portfolio"]["guardrails"]["active"]
    assert active["known_value"] == "1300"
    assert active["value"] is None
    assert active["status"] == "breached"


def test_current_breach_cannot_be_described_as_an_approved_exception():
    answer = recommendation()
    answer.update(
        preferred_action="no_action", reason="Conviction permits an exception to the cap."
    )
    response = review(settings={"single_company_cap": "0.5"}, answer=answer)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recommendation"]["preferred_action"] == "review_only"
    assert "not approved exceptions" in result["recommendation"]["reason"]


@pytest.mark.parametrize(
    "field", ["reason", "downside", "assumptions", "uncertainty", "what_could_change"]
)
@pytest.mark.parametrize("context", ["current", "blocked_proposal", "unknown_proposal"])
def test_guardrail_waivers_are_removed_from_every_displayed_answer_field(field, context):
    answer = recommendation()
    waiver = "Strong conviction permits an exception to configured limits."
    answer[field] = waiver if field in {"reason", "downside"} else [waiver]
    portfolio = snapshot()
    changes = None if context == "current" else {"new_cash": [], "trades": []}
    if context == "unknown_proposal":
        portfolio["fx"] = []
    response = review(
        portfolio, settings={"single_company_cap": "0.5"}, proposed_changes=changes, answer=answer
    )
    assert response.status_code == 200, response.text
    assert waiver not in response.text
    if changes is not None:
        assert response.json()["proposals"][0]["status"] == (
            "unknown" if context == "unknown_proposal" else "blocked"
        )


def test_unknown_indirect_cap_cannot_be_presented_as_cleared():
    portfolio = classified_portfolio()
    answer = recommendation()
    answer["reason"] = "The portfolio is fully cleared against the company cap."
    response = review(
        portfolio,
        {"single_company_cap": "0.5", "indirect_cap_policy": "include_known_indirect"},
        answer=answer,
    )
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["preferred_action"] == "wait_for_inputs"
    assert answer["reason"] not in response.text
