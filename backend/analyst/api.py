import csv
import io

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from openai import OpenAIError

from .allocation import DiscoveryProvider
from .config import Settings
from .csv_input import COLUMNS, load_csv
from .financial_data import FinancialProvider, PersonalFinancialProvider
from .pipeline import InvalidReview, analyze
from .providers import DataProvider, ModelProvider, OpenAIModel, SuppliedDataProvider
from .research import ResearchProvider, ReviewedResearchProvider
from .schemas import AnalysisRequest, AnalysisResult, CSVRequest, Snapshot


def create_app(
    *,
    model: ModelProvider | None = None,
    data: DataProvider | None = None,
    settings: Settings | None = None,
    financial: FinancialProvider | None = None,
    research: ResearchProvider | None = None,
    discovery: DiscoveryProvider | None = None,
) -> FastAPI:
    app = FastAPI(title="Personal Investment Analyst", version="0.1.0")
    source = data or SuppliedDataProvider()

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
        except (ValueError, csv.Error):
            raise HTTPException(
                422,
                "Invalid portfolio CSV. Check the template columns, row types, dates, quantities and references.",
            ) from None

    @app.post("/api/analyze", response_model=AnalysisResult)
    async def analysis(request: AnalysisRequest) -> AnalysisResult:
        config = settings or Settings.from_environment()
        provider = model
        if provider is None:
            if not config.api_key or not config.model:
                raise HTTPException(
                    503, "Configure the backend OpenAI API key and model to analyze."
                )
            provider = OpenAIModel(config)
        try:
            financial_source = financial or PersonalFinancialProvider.from_environment()
            research_source = research or (ReviewedResearchProvider.from_environment() if request.stock or request.new_cash or request.portfolio_review else None)
            return await analyze(request, provider, source, secret=config.api_key, financial=financial_source, research=research_source, discovery=discovery)
        except (ValueError, OSError):
            raise HTTPException(503, "Backend financial source configuration is invalid.") from None
        except InvalidReview:
            raise HTTPException(
                502,
                "The model did not produce a valid portfolio review. No recommendation was completed.",
            ) from None
        except OpenAIError:
            raise HTTPException(
                502, "The model service could not complete the review. Try again."
            ) from None
        finally:
            if isinstance(provider, OpenAIModel):
                await provider.client.close()

    return app


app = create_app()
