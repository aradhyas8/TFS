"""Runs the analysis pipeline the workflow tools run, for tests of its validation and results.

Desk forms now reach the pipeline through thread runs (`run_*` workflow tools). These tests exercise the pipeline
itself with a supplied portfolio and a scripted model, and read the outcome the way the removed `/api/analyze`
endpoint reported it: the result, or a status code with the shared safe failure message.
"""

import asyncio
import json
from typing import Any

import httpx
from openai import OpenAIError
from pydantic import ValidationError

from analyst.allocation import DiscoveryProvider
from analyst.config import Settings
from analyst.failures import describe_failure
from analyst.financial_data import FinancialProvider, PersonalFinancialProvider
from analyst.issuer_research import IssuerResearchProvider
from analyst.pipeline import InvalidReview, analyze
from analyst.providers import (
    DataProvider,
    ModelProvider,
    OpenAIModel,
    OpenAIResponseTransport,
    SuppliedDataProvider,
)
from analyst.research import ResearchProvider
from analyst.schemas import AnalysisRequest
from analyst.sec_research import SecResearchProvider, SecUnavailable

# The status the HTTP layer gave each failure kind; rate and plan limits were model-service failures (502).
STATUS = {"incomplete": 502, "invalid_output": 502, "rate_limit": 502, "plan_limit": 502, "timeout": 504,
          "unavailable": 503, "model_service": 502, "internal": 500}


class Outcome:
    """A pipeline outcome with the status and body the HTTP layer used to give it."""

    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self.body = body

    def json(self) -> dict[str, Any]:
        return self.body

    @property
    def text(self) -> str:
        return json.dumps(self.body)


def analyze_request(
    body: dict[str, Any], *, model: ModelProvider | None = None, data: DataProvider | None = None,
    financial: FinancialProvider | None = None, research: ResearchProvider | None = None,
    discovery: DiscoveryProvider | None = None, settings: Settings | None = None,
) -> Outcome:
    try:
        request = AnalysisRequest.model_validate(body)
    except ValidationError as error:
        return Outcome(422, {"detail": "Invalid dated portfolio or question.",
                             "fields": [".".join(str(part) for part in item["loc"]) for item in error.errors()]})
    config = settings or Settings.from_environment()
    needs_ai = not (request.theme and not request.theme.confirmed)
    provider = model if model is not None or not needs_ai else OpenAIModel(config)
    research_source = research or (
        IssuerResearchProvider.from_environment(SecResearchProvider.from_environment())
        if request.stock or request.portfolio_review or request.new_cash or (request.theme and request.theme.confirmed)
        else None)

    async def run() -> Any:
        try:
            return await analyze(request, provider, data or SuppliedDataProvider(), secret=config.api_key,
                                 financial=financial or PersonalFinancialProvider.from_environment(),
                                 research=research_source, discovery=discovery)
        finally:
            if isinstance(provider, OpenAIModel):
                await provider.client.close()

    try:
        result = asyncio.run(run())
    except InvalidReview:
        return Outcome(502, {"detail": "The model did not produce a valid portfolio review. No recommendation was completed."})
    except (OpenAIError, ValueError, OSError, httpx.HTTPError, SecUnavailable, TimeoutError) as error:
        failure = describe_failure(error)
        return Outcome(STATUS[failure.kind], {"detail": failure.message})
    return Outcome(200, result.model_dump(mode="json"))


def analyze_with(client: Any, body: dict[str, Any]) -> Outcome:
    """The same pipeline run with the providers an app was created with (its orchestrator holds them)."""
    orchestrator = client.app.state.orchestrator
    transport = orchestrator.transport
    return analyze_request(
        body, model=None if isinstance(transport, OpenAIResponseTransport) else transport, data=orchestrator.data,
        financial=orchestrator.financial, research=orchestrator.research, discovery=orchestrator.discovery,
        settings=orchestrator.settings)
