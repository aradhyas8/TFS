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
    first_review = first.json()["portfolio"]
    second_review = second.json()["portfolio"]
    # Equivalent inputs have the same dated values and provenance; each request
    # records its own review timestamp rather than pretending both ran together.
    assert first_review.pop("reviewed_at")
    assert second_review.pop("reviewed_at")
    assert second_review == first_review


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


def test_csv_import_with_empty_or_none_as_of_infers_date_from_csv():
    from pathlib import Path

    csv_text = (Path(__file__).resolve().parents[2] / "examples/portfolio.csv").read_text(
        encoding="utf-8"
    )
    client = TestClient(create_app(data=FakeDataProvider()))

    # Case 1: as_of is empty string (as sent by UI when date picker is blank)
    res1 = client.post(
        "/api/portfolio/csv",
        json={"csv": csv_text, "as_of": "", "reporting_currency": "CAD"},
    )
    assert res1.status_code == 200, res1.text
    assert res1.json()["as_of"] == "2026-09-30"

    # Case 2: as_of is omitted / None
    res2 = client.post(
        "/api/portfolio/csv",
        json={"csv": csv_text, "reporting_currency": "CAD"},
    )
    assert res2.status_code == 200, res2.text
    assert res2.json()["as_of"] == "2026-09-30"


def test_csv_import_with_explicit_as_of_overrides():
    from pathlib import Path

    csv_text = (Path(__file__).resolve().parents[2] / "examples/portfolio.csv").read_text(
        encoding="utf-8"
    )
    client = TestClient(create_app(data=FakeDataProvider()))
    res = client.post(
        "/api/portfolio/csv",
        json={"csv": csv_text, "as_of": "2026-10-15", "reporting_currency": "CAD"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["as_of"] == "2026-10-15"


def test_csv_import_without_as_of_and_no_dates_in_csv_raises_clear_error():
    # Cash-only CSV with no mark_date or fx_date
    cash_csv = (
        "row_type,id,account_id,account_name,ticker,listing,company_id,company_name,shares,cash,currency,mark,mark_date,mark_source,to_currency,fx_rate,fx_date,fx_source\n"
        "account,,tfsa,TFSA,,,,,,,,,,,,$$\n".replace("$$", ",,,,,,,,,,,,,,")
    )
    # Use exact column count
    header = "row_type,id,account_id,account_name,ticker,listing,company_id,company_name,shares,cash,currency,mark,mark_date,mark_source,to_currency,fx_rate,fx_date,fx_source"
    row1 = "account,,tfsa,TFSA,,,,,,,,,,,,,,"
    row2 = "cash,c1,tfsa,TFSA,,,,,,1000,CAD,,,,,,,"
    cash_csv = f"{header}\n{row1}\n{row2}\n"
    client = TestClient(create_app(data=FakeDataProvider()))
    res = client.post(
        "/api/portfolio/csv",
        json={"csv": cash_csv, "as_of": "", "reporting_currency": "CAD"},
    )
    assert res.status_code == 422, res.text
    assert "A snapshot as-of date is required when not present in the CSV." in res.json()["detail"]


def test_validate_prose_allows_negated_guarantee_and_disclaimed_contribution_room():
    import pytest

    from analyst.pipeline import InvalidReview, validate_prose

    # Permitted disclaimers:
    validate_prose(
        "Cash rates could be more supportive temporarily, but no rate is guaranteed. Opportunity costs remain if equities outperform."
    )
    validate_prose("Returns are not guaranteed. Risk capacity does not authorize waiving limits.")
    validate_prose(
        "Tax effects, contribution room, transaction costs and actual cash interest terms are unknown.",
        stock=True,
    )
    validate_prose("Account contribution room is unverified and not modeled.", stock=True)

    # Forbidden positive claims:
    with pytest.raises(InvalidReview):
        validate_prose("The return is guaranteed over the forecast period.")
    with pytest.raises(InvalidReview):
        validate_prose("You have remaining contribution room to deploy.", stock=True)


def test_validate_prose_allows_explicit_not_trade_instructions_disclaimer():
    from analyst.pipeline import InvalidReview, validate_prose

    validate_prose(
        "These are review findings, not trade instructions. Concentration could distort estimated weights."
    )
    validate_prose(
        "Treat these as review flags, not approved exceptions or trade instructions. Cap status may differ on live marks."
    )

    with pytest.raises(InvalidReview):
        validate_prose("You should trade the concentrated holding.")
    with pytest.raises(InvalidReview):
        validate_prose("Consider a trade to reduce concentration.")


def test_validate_prose_allows_negated_probability_disclaimers():
    from analyst.pipeline import InvalidReview, validate_prose

    # Permitted negative disclaimers
    validate_prose("This conditional path is not a probability-weighted forecast.")
    validate_prose("Cases are conditional, without probabilities or a weighted expected value.")
    validate_prose("No probabilities are assigned to these paths.")
    validate_prose("These paths do not reflect probabilities.")
    validate_prose("Outcomes are not probability-weighted.")
    validate_prose("Probabilities are not modeled.")

    # Forbidden positive claims
    with pytest.raises(InvalidReview):
        validate_prose("There is a high probability of capital appreciation.")
    with pytest.raises(InvalidReview):
        validate_prose("We assign a low probability to the downside case.")


def test_validate_prose_allows_descriptive_trading_properties():
    from analyst.pipeline import InvalidReview, validate_prose

    # Permitted descriptive trading terminology
    validate_prose(
        "This is a gross total-return path in the fund's stated CAD trading currency, with distributions reinvested."
    )
    validate_prose("The fund trades on the TSX under ticker XIC.")
    validate_prose("Average daily trading volume remains unverified.")
    validate_prose("The ETF trading symbol is confirmed.")

    # Forbidden execution directions
    with pytest.raises(InvalidReview):
        validate_prose("You should trade this position immediately.")
    with pytest.raises(InvalidReview):
        validate_prose("Begin trading the synthetic position to balance weights.")
    with pytest.raises(InvalidReview):
        validate_prose("Execute a buy order for the ETF.")


def test_research_source_selection_for_new_cash_and_theme(monkeypatch):
    import analyst.api
    from analyst.financial_data import FakeFinancialProvider
    from analyst.issuer_research import IssuerResearchProvider
    from analyst.schemas import AnalysisResult, FinancialEvidence, Recommendation
    from analyst.sec_research import SecResearchProvider
    from tests.test_allocation import allocation_evidence, allocation_request
    from tests.test_theme import theme_request

    captured: list[object] = []

    async def fake_analyze(request, provider, source, *, secret, financial, research, discovery):
        from analyst.calculations import review_portfolio
        from analyst.schemas import ThemeResult

        captured.append(research)
        theme_res = (
            ThemeResult(context=request.theme, status="awaiting_agreement")
            if request.theme and not request.theme.confirmed
            else None
        )
        return AnalysisResult(
            question=request.question,
            portfolio=review_portfolio(request.portfolio, FinancialEvidence()),
            recommendation=Recommendation.model_validate(recommendation()),
            status="completed",
            theme=theme_res,
        )

    monkeypatch.setattr(analyst.api, "analyze", fake_analyze)
    client = TestClient(
        create_app(
            model=ScriptedModel([]),
            data=FakeDataProvider(),
            financial=FakeFinancialProvider(allocation_evidence()),
        )
    )

    # 1. New Cash request
    resp_cash = client.post("/api/analyze", json=allocation_request())
    assert resp_cash.status_code == 200, resp_cash.text
    assert len(captured) == 1
    cash_research = captured[0]
    assert isinstance(cash_research, IssuerResearchProvider)
    assert isinstance(cash_research.fallback, SecResearchProvider)

    # 2. Confirmed Theme request
    resp_theme = client.post("/api/analyze", json=theme_request(confirmed=True))
    assert resp_theme.status_code == 200, resp_theme.text
    assert len(captured) == 2
    theme_research = captured[1]
    assert isinstance(theme_research, IssuerResearchProvider)
    assert isinstance(theme_research.fallback, SecResearchProvider)

    # 3. Unconfirmed Theme request (research is None, makes no model or research calls)
    resp_unconfirmed = client.post("/api/analyze", json=theme_request(confirmed=False))
    assert resp_unconfirmed.status_code == 200, resp_unconfirmed.text
    assert len(captured) == 3
    assert captured[2] is None
    assert resp_unconfirmed.json()["theme"]["status"] == "awaiting_agreement"
