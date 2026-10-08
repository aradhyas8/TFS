import csv
import io
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from openai import OpenAIError
from pydantic import ValidationError

from .allocation import DiscoveryProvider
from .config import Settings
from .csv_input import COLUMNS, load_csv
from .decisions import DecisionStore, extract_decision
from .financial_data import FinancialProvider, PersonalFinancialProvider, eodhd_symbol
from .pipeline import InvalidReview, analyze
from .portfolio import HOLDINGS_COLUMNS, PortfolioStore, enrich, identify, load_holdings
from .providers import DataProvider, ModelProvider, OpenAIModel, SuppliedDataProvider
from .research import ResearchProvider, ReviewedResearchProvider
from .schemas import (
    AnalysisRequest,
    AnalysisResult,
    ConfirmActionRequest,
    CSVRequest,
    FinancialEvidence,
    IdentifyHolding,
    RefreshPrices,
    SavedDecision,
    SaveDecisionRequest,
    SavedPortfolio,
    Snapshot,
)


def create_app(
    *,
    model: ModelProvider | None = None,
    data: DataProvider | None = None,
    settings: Settings | None = None,
    financial: FinancialProvider | None = None,
    research: ResearchProvider | None = None,
    discovery: DiscoveryProvider | None = None,
    store: DecisionStore | None = None,
    portfolio_store: PortfolioStore | None = None,
) -> FastAPI:
    app = FastAPI(title="Personal Investment Analyst", version="0.1.0")
    source = data or SuppliedDataProvider()
    decisions = store or DecisionStore()
    portfolios = portfolio_store or PortfolioStore()

    @app.exception_handler(RequestValidationError)
    async def invalid_input(_request: Request, error: RequestValidationError) -> JSONResponse:
        # Omit input values and validation context from errors; never reflect payloads.
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Invalid dated portfolio or question.",
                "fields": [".".join(str(part) for part in item["loc"]) for item in error.errors()],
            },
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/portfolio/template")
    def portfolio_template() -> Response:
        buffer = io.StringIO()
        csv.writer(buffer).writerow(COLUMNS)
        return Response(
            buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="portfolio-template.csv"'},
        )

    @app.post("/api/portfolio/csv", response_model=Snapshot)
    def portfolio_csv(request: CSVRequest) -> Snapshot:
        try:
            return load_csv(request)
        except (ValueError, csv.Error) as exc:
            msg = str(exc)
            raise HTTPException(
                422,
                f"Invalid portfolio CSV: {msg}" if msg else "Invalid portfolio CSV. Check the template columns, row types, dates, quantities and references.",
            ) from None

    @app.get("/api/portfolio/holdings-template")
    def holdings_template() -> Response:
        return Response(",".join(HOLDINGS_COLUMNS) + "\r\nTFSA,XEQT.TO,120,31.50,,etf\r\nTFSA,AAPL,10,,,\r\n", media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="holdings-template.csv"'})

    @app.get("/api/portfolio", response_model=SavedPortfolio | None)
    async def current_portfolio() -> SavedPortfolio | None:
        # A portfolio saved before the resolver improved is upgraded in place; holdings, costs and rules are untouched.
        current = portfolios.get()
        if current is None:
            return None
        upgraded = await enrich(current, market_data())
        return portfolios.save(upgraded) if upgraded != current else current

    @app.post("/api/portfolio/import", response_model=SavedPortfolio)
    async def import_portfolio(request: CSVRequest) -> SavedPortfolio:
        try:
            imported = load_holdings(request, reference_identities())
        except (ValueError, csv.Error) as exc:
            message = exc.errors()[0]["msg"].removeprefix("Value error, ") if isinstance(exc, ValidationError) else str(exc)
            raise HTTPException(422, f"Invalid portfolio CSV: {message}") from None
        imported = await enrich(imported, market_data())
        # New holdings replace the old ones; the user's rules stay.
        current = portfolios.get()
        return portfolios.save(imported.model_copy(update={"settings": current.settings if current else None}))

    @app.put("/api/portfolio", response_model=SavedPortfolio)
    def update_portfolio(request: SavedPortfolio) -> SavedPortfolio:
        return portfolios.save(request)

    @app.post("/api/portfolio/identify", response_model=SavedPortfolio)
    async def identify_holding(request: IdentifyHolding) -> SavedPortfolio:
        current = portfolios.get()
        if current is None:
            raise HTTPException(404, "No saved portfolio.")
        try:
            answered = identify(current, request, reference_identities())
        except ValueError as exc:
            message = exc.errors()[0]["msg"].removeprefix("Value error, ") if isinstance(exc, ValidationError) else str(exc)
            raise HTTPException(422, message) from None
        return portfolios.save(await enrich(answered, market_data()))

    @app.post("/api/market/refresh")
    async def refresh_prices(request: RefreshPrices) -> dict[str, Any]:
        """Fills the local quote cache for the saved holdings. Analyses only read that cache."""
        current = portfolios.get()
        provider = market_data()
        if current is None or not isinstance(provider, PersonalFinancialProvider):
            raise HTTPException(404, "No saved portfolio.")
        result = await provider.refresh_quotes(current.snapshot.positions, force=request.force)
        cached = provider.cache.get() if provider.cache else {}
        result["quotes"] = {position.id: cached[symbol].model_dump(mode="json") for position in current.snapshot.positions
                            if (symbol := eodhd_symbol(position)) and symbol in cached}
        return result

    def market_data() -> FinancialProvider | None:
        try:
            return financial or PersonalFinancialProvider.from_environment()
        except (ValueError, OSError):
            return None

    def reference_identities() -> FinancialEvidence | None:
        return getattr(market_data(), "reference", None)

    @app.post("/api/analyze", response_model=AnalysisResult)
    async def analysis(request: AnalysisRequest) -> AnalysisResult:
        config = settings or Settings.from_environment()
        provider = model
        if provider is None and not (request.theme and not request.theme.confirmed):
            if not config.api_key or not config.model:
                raise HTTPException(
                    503, "Configure the backend OpenAI API key and model to analyze."
                )
            provider = OpenAIModel(config)
        try:
            financial_source = financial or PersonalFinancialProvider.from_environment()
            research_source = research or (ReviewedResearchProvider.from_environment() if request.stock or request.new_cash or request.portfolio_review or (request.theme and request.theme.confirmed) else None)
            return await analyze(request, provider, source, secret=config.api_key, financial=financial_source, research=research_source, discovery=discovery)
        except (ValueError, OSError):
            raise HTTPException(503, "Backend financial source configuration is invalid.") from None
        except InvalidReview as exc:
            import logging
            logging.error("InvalidReview in /api/analyze: %s", exc, exc_info=True)
            raise HTTPException(
                502,
                "The model did not produce a valid portfolio review. No recommendation was completed.",
            ) from None

        except OpenAIError as exc:
            import logging
            logging.error("OpenAI request failed: type=%s status=%s request_id=%s", type(exc).__name__, getattr(exc, "status_code", None), getattr(exc, "request_id", None))
            raise HTTPException(
                502, "The model service could not complete the review. Try again."
            ) from None
        finally:
            if isinstance(provider, OpenAIModel):
                await provider.client.close()

    @app.get("/api/decisions", response_model=list[SavedDecision])
    def list_decisions() -> list[SavedDecision]:
        return decisions.list_decisions()

    @app.post("/api/decisions", response_model=SavedDecision)
    def save_decision(request: SaveDecisionRequest) -> SavedDecision:
        if request.result is not None:
            record = extract_decision(request.result, confirmed_action=request.confirmed_action)
        elif request.decision is not None:
            record = request.decision
            if request.confirmed_action is not None:
                record = record.model_copy(update={"confirmed_action": request.confirmed_action})
        else:
            raise HTTPException(422, "Missing decision content.")
        return decisions.save(record)

    @app.get("/api/decisions/{decision_id}", response_model=SavedDecision)
    def get_decision(decision_id: str) -> SavedDecision:
        record = decisions.get(decision_id)
        if record is None:
            raise HTTPException(404, "Decision not found.")
        return record

    @app.delete("/api/decisions/{decision_id}", status_code=204)
    def delete_decision(decision_id: str) -> None:
        if not decisions.delete(decision_id):
            raise HTTPException(404, "Decision not found.")

    @app.delete("/api/decisions")
    def clear_decisions() -> dict[str, int]:
        return {"deleted": decisions.clear()}

    @app.post("/api/decisions/{decision_id}/confirm", response_model=SavedDecision)
    def confirm_decision_action(decision_id: str, request: ConfirmActionRequest) -> SavedDecision:
        try:
            return decisions.confirm_action(decision_id, action=request.action, notes=request.notes)
        except KeyError:
            raise HTTPException(404, "Decision not found.")

    return app



app = create_app()
