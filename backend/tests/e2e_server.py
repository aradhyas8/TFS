"""Only imported by Playwright's local test server; production has no fake-mode flag."""

import asyncio
import json
import socket
from typing import Any

from analyst.api import create_app
from analyst.config import Settings
from analyst.financial_data import FakeFinancialProvider
from analyst.providers import FakeDataProvider, ModelTurn, ToolCall
from analyst.schemas import FinancialEvidence, Snapshot
from tests.test_comparison import case_drivers, driver, judgments
from tests.test_freshness import evidence_fixture

# No network outside the local test servers, even if a provider is changed accidentally.
original_connect = socket.socket.connect


def local_connect(sock: socket.socket, address: Any) -> None:
    if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
        raise AssertionError("E2E tests must not contact live services.")
    original_connect(sock, address)


socket.socket.connect = local_connect  # type: ignore[method-assign]


class BrowserTestModel:
    async def respond(self, messages: list[dict[str, Any]], *, require_tool: bool) -> ModelTurn:
        if require_tool:
            if json.loads(messages[1]["content"])["question"] == "Slow model review":
                await asyncio.sleep(31)
            return ModelTurn(calls=[ToolCall("browser_tool", "review_portfolio", "{}")])
        request = json.loads(messages[1]["content"])
        tool = json.loads(messages[-1]["output"])
        if request.get("comparison") and "total_value" in tool:
            template = judgments()["alternatives"][0]["cases"]
            positions = {row["id"]: row for row in request["portfolio"]["positions"]}
            selected = []
            for alt in request["comparison"]["alternatives"]:
                ids = request["comparison"]["scope_position_ids"] if alt["kind"] == "no_action" else [alt["position_id"]]
                drivers = [driver(key, cash=positions[key]["kind"] == "cash") for key in ids]
                selected.append({"alternative_id": alt["id"], "cases": [
                    {**case, "drivers": case_drivers(drivers, case["name"])} for case in template
                ]})
            return ModelTurn(calls=[ToolCall("browser_comparison", "calculate_comparison", json.dumps({"alternatives": selected}))])
        if request["question"] == "Check model proposal" and "total_value" in tool:
            return ModelTurn(calls=[ToolCall("browser_proposal", "check_proposed_changes", json.dumps({
                "new_cash": [], "trades": [{"position_id": "p2", "shares_change": "2", "cash_position_id": "c2"}],
            }))])
        # Actual deterministic tool output has to exist; this provider cannot skip it.
        assert "total_value" in tool and "direct_companies" in tool or "post_total_value" in tool and "guardrails" in tool or "alternatives" in tool and "calculation_basis" in tool
        answer = {
            "preferred_action": "review_only",
            "amount": None,
            "reason": f"Regarding '{request['question']}': review the supplied direct company exposure across accounts.",
            "alternatives": [
                {
                    "action": "clarify_inputs",
                    "reason": "Supply a baseline and personal risk context.",
                }
            ],
            "downside": "Company-specific losses can affect concentrated holdings.",
            "assumptions": ["Supplied marks represent this dated snapshot."],
            "uncertainty": ["Personal guardrails and indirect fund overlap remain unknown."],
            "what_could_change": [
                "Verified identities, updated marks or risk context could change the review."
            ],
        }
        if request["question"] == "Return invalid output":
            answer["amount"] = "1000"
        if request["question"] == "Check model proposal":
            answer.update(preferred_action="no_action", reason="Strong conviction permits an exception to configured limits.")
            answer["downside"] = "Strong conviction permits an exception in the downside explanation."
        if request.get("comparison"):
            answer.update(
                preferred_action="no_action",
                reason="The compared cases do not establish a superior action while fund exposure evidence and personal risk context remain provisional. Retaining the actual holdings and cash is a conditional alternative.",
                alternatives=[{"action": "clarify_inputs", "reason": "Confirm fund exposure, costs, income and personal risk context before deciding."}],
                downside="The diversified fund retains equity and currency downside; cash faces falling reinvestment rates. No action retains the existing mix and its risks.",
                assumptions=["Exposure, income, rate and currency paths are judgments; calculations use the dated common capital basis."],
                uncertainty=["Unknown transaction costs and personal tax consequences leave terminal values incomplete."],
                what_could_change=["Supported fund facts, revised rate or currency paths, and supplied risk context could distinguish the alternatives."],
            )
        return ModelTurn(answer=answer)


financial = FakeFinancialProvider()


class BrowserTestData(FakeDataProvider):
    def snapshot(self, supplied: Snapshot) -> Snapshot:
        # Browser journeys run serially. Bind the external source fixture to the
        # submitted listing; every request resets it, including ordinary broker marks.
        financial.reference = FinancialEvidence()
        for position in supplied.positions:
            if position.id == "p1" and position.mark and position.mark.source.startswith("Fixture "):
                fixture = evidence_fixture()
                scenario = position.mark.source.removeprefix("Fixture ")
                if scenario == "ambiguous":
                    fixture["identities"]["p1"]["status"] = "ambiguous"
                else:
                    fixture["quotes"]["p1"]["status"] = scenario
                financial.reference = FinancialEvidence.model_validate(fixture)
        return super().snapshot(supplied)


app = create_app(
    model=BrowserTestModel(),
    data=BrowserTestData(),
    financial=financial,
    settings=Settings("sk-test-backend-only-never-browser", "test-model"),
)
