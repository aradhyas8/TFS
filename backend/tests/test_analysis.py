import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.config import Settings
from analyst.providers import FakeDataProvider, ModelTurn, ScriptedModel, ToolCall


def snapshot():
    return {
        "as_of": "2026-09-30",
        "reporting_currency": "CAD",
        "accounts": [{"id": "tfsa", "name": "TFSA"}, {"id": "broker", "name": "Brokerage"}],
        "positions": [
            {
                "id": "p1",
                "account_id": "tfsa",
                "kind": "stock",
                "ticker": "ACME",
                "listing": "XNAS",
                "company_id": "acme",
                "company_name": "Acme",
                "shares": "10",
                "currency": "USD",
                "mark": {"value": "100", "as_of": "2026-09-30", "source": "Broker display"},
            },
            {
                "id": "p2",
                "account_id": "broker",
                "kind": "stock",
                "ticker": "ACME",
                "listing": "XTSE",
                "company_id": "acme",
                "company_name": "Acme",
                "shares": "5",
                "currency": "CAD",
                "mark": {"value": "200", "as_of": "2026-09-30", "source": "Manual mark"},
            },
            {"id": "c1", "account_id": "tfsa", "kind": "cash", "cash": "650", "currency": "CAD"},
            {"id": "c2", "account_id": "broker", "kind": "cash", "cash": "500", "currency": "USD"},
        ],
        "fx": [
            {
                "from_currency": "USD",
                "to_currency": "CAD",
                "rate": "1.3",
                "as_of": "2026-09-30",
                "source": "Supplied FX",
            }
        ],
    }


def recommendation():
    return {
        "preferred_action": "review_only",
        "amount": None,
        "reason": "Review direct issuer concentration across both accounts.",
        "alternatives": [
            {"action": "clarify_inputs", "reason": "Supply a baseline before considering changes."}
        ],
        "downside": "Direct company exposure can amplify company-specific losses.",
        "assumptions": ["Supplied marks describe the dated snapshot."],
        "uncertainty": ["Risk preferences and indirect fund overlap are unknown."],
        "what_could_change": [
            "A verified snapshot or supplied risk context could change the review."
        ],
    }


def test_question_receives_computed_whole_portfolio_review():
    model = ScriptedModel(
        [
            ModelTurn(calls=[ToolCall("call_1", "review_portfolio", "{}")]),
            ModelTurn(answer=recommendation()),
        ]
    )
    data = FakeDataProvider()
    client = TestClient(create_app(model=model, data=data))
    response = client.post(
        "/api/analyze", json={"question": "How concentrated am I?", "portfolio": snapshot()}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["question"] == "How concentrated am I?"
    assert result["portfolio"]["total_value"] == "3600"
    assert result["portfolio"]["holdings_value"] == "2300"
    assert result["portfolio"]["cash_value"] == "1300"
    assert [p["value"] for p in result["portfolio"]["positions"]] == ["1300", "1000", "650", "650"]
    assert result["portfolio"]["positions"][0]["weight"] == "0.36111111"
    assert [a["total_value"] for a in result["portfolio"]["accounts"]] == ["1950", "1650"]
    assert result["portfolio"]["direct_companies"][0]["value"] == "2300"
    assert result["portfolio"]["direct_companies"][0]["weight"] == "0.63888889"
    assert result["recommendation"]["amount"] is None
    assert result["portfolio"]["baseline"] is None
    assert result["portfolio"]["guardrails"] is None
    assert result["status"] == "completed"
    # Inspect the external fake provider's transcript: request and actual tool output.
    assert "How concentrated am I?" in str(model.requests[0])
    assert "Brokerage" in str(model.requests[0])
    assert '"total_value": "3600"' in str(model.requests[1])
    assert data.snapshots[0].as_of.isoformat() == "2026-09-30"


@pytest.mark.parametrize(
    "turns",
    [
        [ModelTurn(answer=recommendation())],
        [ModelTurn(calls=[ToolCall("bad", "unknown_tool", "{}")])],
        [ModelTurn(calls=[ToolCall("bad", "review_portfolio", '{"total_value": "999"}')])],
        [ModelTurn(calls=[ToolCall("bad", "review_portfolio", "[]")])],
        [ModelTurn(calls=[ToolCall("bad", "review_portfolio", "{broken")])],
        [ModelTurn(calls=[ToolCall("same", "review_portfolio", "{}")])] * 3,
    ],
)
def test_invalid_tool_calls_cannot_complete_a_review(turns):
    response = TestClient(create_app(model=ScriptedModel(turns), data=FakeDataProvider())).post(
        "/api/analyze", json={"question": "Review my exposure", "portfolio": snapshot()}
    )
    assert response.status_code == 502
    assert "recommendation" not in response.json()


@pytest.mark.parametrize(
    "patch",
    [
        {"amount": "1000"},
        {"total_value": "999999"},
        {"preferred_action": "buy"},
        {"reason": "Buy more company shares."},
        {"reason": "Company exposure is 99%."},
        {"reason": "This is guaranteed."},
        {"alternatives": []},
        {"downside": ""},
        {"reason": "sk-test-backend-only-never-browser"},
        {"reason": "Increase Acme to half your portfolio."},
        {"reason": "Put all your cash into Acme."},
    ],
)
def test_unvalidated_model_claims_do_not_replace_calculated_values(patch):
    answer = {**recommendation(), **patch}
    turns = [
        ModelTurn(calls=[ToolCall("call", "review_portfolio", "{}")]),
        ModelTurn(answer=answer),
    ]
    response = TestClient(create_app(model=ScriptedModel(turns), data=FakeDataProvider())).post(
        "/api/analyze", json={"question": "Review my exposure", "portfolio": snapshot()}
    )
    assert response.status_code == 502
    assert "portfolio" not in response.json()
    assert "sk-test-backend-only-never-browser" not in response.text


@pytest.mark.parametrize(
    "fault",
    [
        "negative_shares",
        "nan",
        "negative_fx",
        "duplicate_position",
        "missing_account",
        "conflicting_issuer",
    ],
)
def test_invalid_portfolio_is_rejected_at_application_boundary(fault):
    portfolio = snapshot()
    if fault == "negative_shares":
        portfolio["positions"][0]["shares"] = "-1"
    elif fault == "nan":
        portfolio["positions"][0]["mark"]["value"] = "NaN"
    elif fault == "negative_fx":
        portfolio["fx"][0]["rate"] = "0"
    elif fault == "duplicate_position":
        portfolio["positions"][1]["id"] = "p1"
    elif fault == "missing_account":
        portfolio["positions"][0]["account_id"] = "unknown"
    else:
        portfolio["positions"][1]["listing"] = "XNAS"
        portfolio["positions"][1]["company_id"] = "other-issuer"
    response = TestClient(create_app(model=ScriptedModel([]), data=FakeDataProvider())).post(
        "/api/analyze", json={"question": "Review my exposure", "portfolio": portfolio}
    )
    assert response.status_code == 422
    assert "recommendation" not in response.json()


def test_backend_configuration_does_not_leak_on_failure():
    client = TestClient(create_app(settings=Settings(api_key="", model="")))
    response = client.post(
        "/api/analyze", json={"question": "Review my exposure", "portfolio": snapshot()}
    )
    assert response.status_code == 503
    assert "sk-test-backend-only-never-browser" not in response.text
    assert client.get("/api/health").json() == {"status": "ok"}


def test_explicitly_declining_unsupported_rebalancing_is_a_valid_qualification():
    answer = recommendation()
    answer["reason"] = "No evidence supports a confident allocation, so do not rebalance."
    model = ScriptedModel(
        [
            ModelTurn(calls=[ToolCall("qualification", "review_portfolio", "{}")]),
            ModelTurn(answer=answer),
        ]
    )
    response = TestClient(create_app(model=model, data=FakeDataProvider())).post(
        "/api/analyze", json={"question": "Should I change my exposure?", "portfolio": snapshot()}
    )
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["reason"] == answer["reason"]


def test_empty_account_and_zero_balance_are_preserved_without_defined_weights():
    portfolio = {
        "as_of": "2026-09-30",
        "reporting_currency": "CAD",
        "accounts": [{"id": "a", "name": "Empty"}, {"id": "b", "name": "Zero cash"}],
        "positions": [
            {"id": "c", "account_id": "b", "kind": "cash", "cash": "0", "currency": "CAD"}
        ],
    }
    model = ScriptedModel(
        [
            ModelTurn(calls=[ToolCall("c", "review_portfolio", "{}")]),
            ModelTurn(answer=recommendation()),
        ]
    )
    response = TestClient(create_app(model=model, data=FakeDataProvider())).post(
        "/api/analyze", json={"question": "Review my exposure", "portfolio": portfolio}
    )
    assert response.status_code == 200, response.text
    review = response.json()["portfolio"]
    assert review["total_value"] == "0"
    assert len(review["accounts"]) == 2
    assert review["positions"][0]["weight"] is None


def test_csv_and_manual_snapshots_have_identical_review():
    from pathlib import Path

    manual = snapshot()
    csv_text = (Path(__file__).resolve().parents[2] / "examples/portfolio.csv").read_text(
        encoding="utf-8"
    )
    model = ScriptedModel(
        [
            ModelTurn(calls=[ToolCall("manual", "review_portfolio", "{}")]),
            ModelTurn(answer=recommendation()),
            ModelTurn(calls=[ToolCall("csv", "review_portfolio", "{}")]),
            ModelTurn(answer=recommendation()),
        ]
    )
    client = TestClient(create_app(model=model, data=FakeDataProvider()))
    loaded = client.post(
        "/api/portfolio/csv",
        json={"csv": csv_text, "as_of": "2026-09-30", "reporting_currency": "CAD"},
    )
    assert loaded.status_code == 200, loaded.text
    first = client.post(
        "/api/analyze", json={"question": "Review my exposure", "portfolio": manual}
    )
    second = client.post(
        "/api/analyze", json={"question": "Review my exposure", "portfolio": loaded.json()}
    )
    assert second.status_code == 200, second.text
    assert second.json()["portfolio"] == first.json()["portfolio"]


@pytest.mark.parametrize("missing", ["mark", "listing", "company_id", "fx", "old_mark", "old_fx"])
def test_decisive_missing_facts_remain_unknown(missing):
    portfolio = snapshot()
    if missing in {"mark", "listing", "company_id"}:
        portfolio["positions"][0].pop(missing)
    elif missing == "fx":
        portfolio["fx"] = []
    elif missing == "old_mark":
        portfolio["positions"][0]["mark"]["as_of"] = "2026-09-29"
    else:
        portfolio["fx"][0]["as_of"] = "2026-09-29"
    model = ScriptedModel(
        [
            ModelTurn(calls=[ToolCall("review", "review_portfolio", "{}")]),
            ModelTurn(answer=recommendation()),
        ]
    )
    response = TestClient(create_app(model=model, data=FakeDataProvider())).post(
        "/api/analyze", json={"question": "Can I change my holdings?", "portfolio": portfolio}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["portfolio"]["total_value"] is None
    assert result["portfolio"]["complete"] is False
    assert result["portfolio"]["positions"][0]["value"] is None
    assert all(row["weight"] is None for row in result["portfolio"]["positions"])
    assert result["portfolio"]["positions"][0]["issues"]
    assert result["recommendation"]["amount"] is None


@pytest.mark.parametrize(
    "downside",
    ["Company-specific losses can reduce portfolio value.", "Concentration may increase downside."],
)
def test_explanations_of_portfolio_risk_are_not_execution_directions(downside):
    answer = recommendation()
    answer["downside"] = downside
    model = ScriptedModel(
        [ModelTurn(calls=[ToolCall("risk", "review_portfolio", "{}")]), ModelTurn(answer=answer)]
    )
    response = TestClient(create_app(model=model, data=FakeDataProvider())).post(
        "/api/analyze", json={"question": "What is my risk?", "portfolio": snapshot()}
    )
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["downside"] == downside
