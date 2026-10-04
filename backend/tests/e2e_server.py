"""Only imported by Playwright's local test server; production has no fake-mode flag."""

import asyncio
import json
import socket
from typing import Any

from analyst.api import create_app
from analyst.config import Settings
from analyst.providers import FakeDataProvider, ModelTurn, ToolCall

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
        # Actual deterministic tool output has to exist; this provider cannot skip it.
        assert "total_value" in tool and "direct_companies" in tool
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
        return ModelTurn(answer=answer)


app = create_app(
    model=BrowserTestModel(),
    data=FakeDataProvider(),
    settings=Settings("sk-test-backend-only-never-browser", "test-model"),
)
