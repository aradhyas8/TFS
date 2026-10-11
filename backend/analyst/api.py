import csv
import io
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from openai import OpenAIError
from pydantic import ValidationError

from .allocation import DiscoveryProvider
from .config import Settings
from .csv_input import COLUMNS, load_csv
from .decisions import DecisionStore, extract_decision
from .financial_data import (
    SOURCE_ERRORS,
    FinancialProvider,
    PersonalFinancialProvider,
    eodhd_symbol,
)
from .issuer_research import IssuerResearchProvider
from .orchestrator import Orchestrator
from .persistence import (
    DatabaseLockError,
    InvalidProposalStateError,
    ProposalNotFoundError,
    RunConflictError,
    StaleBaseRevisionError,
    ThreadNotFoundError,
)
from .pipeline import InvalidReview, analyze
from .portfolio import (
    HOLDINGS_COLUMNS,
    PortfolioStore,
    enrich,
    identify,
    listing_currency,
    load_holdings,
    split_ticker,
)
from .portfolio_actions import (
    cancel_portfolio_action,
    confirm_portfolio_action,
    prepare_portfolio_action,
)
from .providers import DataProvider, ModelProvider, OpenAIModel, SuppliedDataProvider
from .research import ResearchProvider
from .schemas import (
    BENCHMARK_FUND,
    AnalysisRequest,
    AnalysisResult,
    ArtifactEnvelope,
    ArtifactReference,
    CancelProposalResult,
    CandidateListing,
    CandidateRequest,
    CandidateResult,
    CandidateUnresolved,
    ConfirmActionRequest,
    ConfirmationResult,
    CreateArtifactRequest,
    CSVRequest,
    FinancialEvidence,
    IdentifyHoldingRequest,
    ImportPortfolioRequest,
    Mark,
    PortfolioProposal,
    PortfolioProposalDetail,
    Position,
    PreparationResult,
    ProposalStatus,
    RefreshPrices,
    RevisionedPortfolio,
    RunRecord,
    SavedDecision,
    SaveDecisionRequest,
    Snapshot,
    SubmitMessageRequest,
    ThreadDetail,
    ThreadSummary,
    UpdatePortfolioRequest,
)
from .sec_research import SecResearchProvider


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
    orchestrator: Orchestrator | None = None,
) -> FastAPI:
    app = FastAPI(title="Personal Investment Analyst", version="0.1.0")
    source = data or SuppliedDataProvider()
    decisions = store or DecisionStore()
    portfolios = portfolio_store or PortfolioStore()
    app_settings = settings or Settings.from_environment()

    # Recover interrupted runs on startup without replaying
    portfolios.db.recover_interrupted_runs()

    # Create orchestrator if provided or lazily initialized
    orch: Orchestrator | None = orchestrator
    if orch is None:
        transport: Any = None
        if model is not None:
            transport = getattr(model, "transport", model)
        else:
            try:
                from .providers import OpenAIResponseTransport
                transport = OpenAIResponseTransport(app_settings, allow_routed=True)
            except ValueError:
                # API key/model not configured at creation time; will error if orchestrator is called
                transport = None
        if transport is not None:
            orch = Orchestrator(db=portfolios.db, transport=transport, settings=app_settings)

    app.state.portfolio_store = portfolios
    app.state.decision_store = decisions
    app.state.orchestrator = orch

    def get_orchestrator() -> Orchestrator:
        nonlocal orch
        if orch is not None:
            return orch
        from .providers import OpenAIResponseTransport
        transport = OpenAIResponseTransport(app_settings, allow_routed=True)
        orch = Orchestrator(db=portfolios.db, transport=transport, settings=app_settings)
        app.state.orchestrator = orch
        return orch

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

    @app.exception_handler(DatabaseLockError)
    async def handle_db_lock(_request: Request, _exc: DatabaseLockError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "Database is temporarily locked. Please retry."},
        )

    @app.exception_handler(ProposalNotFoundError)

    async def handle_proposal_not_found(_request: Request, exc: ProposalNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": str(exc)},
        )

    @app.exception_handler(StaleBaseRevisionError)
    async def handle_stale_base(_request: Request, exc: StaleBaseRevisionError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc)},
        )

    @app.exception_handler(InvalidProposalStateError)
    async def handle_invalid_proposal_state(_request: Request, exc: InvalidProposalStateError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ThreadNotFoundError)
    async def handle_thread_not_found(_request: Request, exc: ThreadNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": str(exc)},
        )

    @app.exception_handler(RunConflictError)
    async def handle_run_conflict(_request: Request, exc: RunConflictError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc)},
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
                f"Invalid portfolio CSV: {msg}"
                if msg
                else "Invalid portfolio CSV. Check the template columns, row types, dates, quantities and references.",
            ) from None

    @app.get("/api/portfolio/holdings-template")
    def holdings_template() -> Response:
        return Response(
            ",".join(HOLDINGS_COLUMNS) + "\r\nTFSA,XEQT.TO,120,31.50,,etf\r\nTFSA,AAPL,10,,,\r\n",
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="holdings-template.csv"'},
        )

    @app.get("/api/portfolio", response_model=RevisionedPortfolio | None)
    def current_portfolio() -> RevisionedPortfolio | None:
        return portfolios.get_revisioned()


    @app.post("/api/portfolio/import", response_model=PreparationResult)
    async def import_portfolio(request: ImportPortfolioRequest) -> PreparationResult:
        try:
            imported = load_holdings(request, reference_identities())
        except (ValueError, csv.Error) as exc:
            message = (
                exc.errors()[0]["msg"].removeprefix("Value error, ")
                if isinstance(exc, ValidationError)
                else str(exc)
            )
            raise HTTPException(422, f"Invalid portfolio CSV: {message}") from None
        imported = await enrich(imported, market_data())
        # New holdings replace the old ones; the user's rules and benchmark stay.
        current = portfolios.get()
        candidate = imported.model_copy(
            update={
                "settings": current.settings if current else None,
                "benchmark": current.benchmark if current else None,
            }
        )
        try:
            return prepare_portfolio_action(
                database=portfolios.db,
                candidate=candidate,
                base_revision=request.base_revision,
                origin="import",
                evidence=reference_identities(),
            )
        except StaleBaseRevisionError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.put("/api/portfolio", response_model=PreparationResult)
    def update_portfolio(request: UpdatePortfolioRequest) -> PreparationResult:
        try:
            return prepare_portfolio_action(
                database=portfolios.db,
                candidate=request.portfolio,
                base_revision=request.base_revision,
                origin="manual",
                evidence=reference_identities(),
            )
        except StaleBaseRevisionError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/portfolio/identify", response_model=PreparationResult)
    async def identify_holding(request: IdentifyHoldingRequest) -> PreparationResult:
        current = portfolios.get()
        if current is None:
            raise HTTPException(404, "No saved portfolio.")
        try:
            answered = identify(current, request, reference_identities())
        except ValueError as exc:
            message = (
                exc.errors()[0]["msg"].removeprefix("Value error, ")
                if isinstance(exc, ValidationError)
                else str(exc)
            )
            raise HTTPException(422, message) from None
        enriched = await enrich(answered, market_data())
        try:
            return prepare_portfolio_action(
                database=portfolios.db,
                candidate=enriched,
                base_revision=request.base_revision,
                origin="identify",
                evidence=reference_identities(),
            )
        except StaleBaseRevisionError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/portfolio/proposals", response_model=list[PortfolioProposal])
    def list_proposals(status: ProposalStatus | None = None) -> list[PortfolioProposal]:
        return portfolios.db.list_proposals(status=status)

    @app.get("/api/portfolio/proposals/{proposal_id}", response_model=PortfolioProposalDetail)
    def get_proposal_detail(proposal_id: str) -> PortfolioProposalDetail:
        detail = portfolios.db.get_proposal_detail(proposal_id)
        if detail is None:
            raise HTTPException(404, f"Proposal '{proposal_id}' not found.")
        return detail

    async def _assert_empty_request(request: Request) -> None:
        body = await request.body()
        if body.strip():
            raise HTTPException(422, "Request body must be empty; replacement payloads are rejected.")

    @app.post("/api/portfolio/proposals/{proposal_id}/confirm", response_model=ConfirmationResult)
    async def confirm_proposal(proposal_id: str, request: Request) -> ConfirmationResult:
        await _assert_empty_request(request)
        return confirm_portfolio_action(portfolios.db, proposal_id)

    @app.post("/api/portfolio/proposals/{proposal_id}/cancel", response_model=CancelProposalResult)
    async def cancel_proposal(proposal_id: str, request: Request) -> CancelProposalResult:
        await _assert_empty_request(request)
        return cancel_portfolio_action(portfolios.db, proposal_id)

    @app.post("/api/portfolio/artifacts", response_model=ArtifactReference)
    def create_portfolio_artifact(request: CreateArtifactRequest) -> ArtifactReference:
        return portfolios.store_hypothetical_artifact(
            request.portfolio,
            label=request.label,
            source_artifact_id=request.source_artifact_id,
            originating_run_id=request.originating_run_id,
        )

    @app.get("/api/portfolio/artifacts/{artifact_id}", response_model=ArtifactReference)
    def get_portfolio_artifact(artifact_id: str) -> ArtifactReference:
        ref = portfolios.get_artifact_reference(artifact_id)
        if not ref:
            raise HTTPException(404, f"Artifact '{artifact_id}' not found.")
        return ref

    @app.get("/api/portfolio/artifacts/{artifact_id}/payload")
    def get_portfolio_artifact_payload(
        artifact_id: str,
        projection: Literal["human", "model"] = "human",
    ) -> dict[str, Any]:
        payload = portfolios.get_artifact(artifact_id, projection=projection)
        if payload is None:
            raise HTTPException(404, f"Artifact '{artifact_id}' not found.")
        return cast(dict[str, Any], payload)

    @app.post("/api/artifacts", response_model=ArtifactReference)
    def create_artifact_alias(request: CreateArtifactRequest) -> ArtifactReference:
        return create_portfolio_artifact(request)

    @app.get("/api/artifacts/{artifact_id}", response_model=ArtifactEnvelope)
    def get_generic_artifact(
        artifact_id: str,
        projection: Literal["human", "model"] = "human",
    ) -> ArtifactEnvelope:
        envelope = portfolios.get_artifact_envelope(artifact_id, projection=projection)
        if envelope is None:
            raise HTTPException(404, f"Artifact '{artifact_id}' not found.")
        return envelope




    @app.post("/api/market/refresh")
    async def refresh_prices(request: RefreshPrices) -> dict[str, Any]:
        """Fills the local quote cache for the saved holdings. Analyses only read that cache."""
        current = portfolios.get()
        provider = market_data()
        if current is None or not isinstance(provider, PersonalFinancialProvider):
            raise HTTPException(404, "No saved portfolio.")
        positions = list(current.snapshot.positions)
        if current.benchmark:
            first_account = (
                current.snapshot.accounts[0].id
                if current.snapshot.accounts
                else "default"
            )
            positions.append(
                Position(
                    id=BENCHMARK_FUND,
                    account_id=first_account,
                    kind="etf",
                    currency=current.benchmark.currency,
                    ticker=current.benchmark.ticker,
                    listing=current.benchmark.listing,
                    company_name=current.benchmark.name,
                    shares=Decimal(0),
                    etf_role="diversified",
                )
            )
        result = await provider.refresh_quotes(positions, force=request.force)
        cached = provider.cache.get() if provider.cache else {}
        result["quotes"] = {
            position.id: cached[symbol].model_dump(mode="json")
            for position in positions
            if (symbol := eodhd_symbol(position)) and symbol in cached
        }
        return result

    @app.post("/api/candidates", response_model=CandidateResult)
    async def candidate_lookup(request: CandidateRequest) -> CandidateResult:
        provider = market_data()
        if provider is None or not hasattr(provider, "lookup"):
            raise HTTPException(503, "Financial provider is not configured.")
        bare_ticker, suffix_listing = split_ticker(request.ticker.strip())
        listing = request.listing or suffix_listing
        currency = listing_currency(listing)
        current = portfolios.get()
        as_of = current.snapshot.as_of if current else datetime.now(UTC).date()
        first_account = (
            current.snapshot.accounts[0].id
            if current and current.snapshot.accounts
            else "default"
        )
        try:
            matches = await provider.lookup(bare_ticker, listing, currency, as_of)
        except SOURCE_ERRORS:
            matches = []

        if not matches:
            return CandidateResult(unresolved=CandidateUnresolved(reason="not_found"))
        if len(matches) > 1:
            listings = [
                CandidateListing(
                    ticker=m.ticker or bare_ticker,
                    listing=m.listing,
                    currency=m.currency,
                    kind=m.kind,
                    company_name=m.company_name,
                )
                for m in matches
            ]
            return CandidateResult(
                unresolved=CandidateUnresolved(reason="multiple", listings=listings)
            )

        match = matches[0]
        pos_listing = match.listing or suffix_listing or "unknown"
        pos_currency = match.currency or listing_currency(pos_listing) or "USD"
        pos = Position(
            id=f"candidate-{match.ticker or bare_ticker}-{pos_listing}",
            account_id=first_account,
            kind=match.kind or "stock",
            currency=pos_currency,
            ticker=match.ticker or bare_ticker,
            listing=pos_listing,
            company_id=match.company_id,
            company_name=match.company_name,
            shares=Decimal(0),
            etf_role="diversified" if match.kind == "etf" else None,
        )

        if hasattr(provider, "refresh_quotes"):
            try:
                await provider.refresh_quotes([pos])
            except SOURCE_ERRORS:
                pass

        cache = getattr(provider, "cache", None)
        if cache:
            try:
                cached = cache.get()
                symbol = eodhd_symbol(pos)
                if symbol and symbol in cached:
                    q = cached[symbol]
                    pos = pos.model_copy(
                        update={
                            "mark": Mark(
                                value=q.value,
                                as_of=q.as_of,
                                source=q.source,
                                captured_at=q.captured_at,
                                basis=q.basis,
                            )
                        }
                    )
            except Exception:
                pass

        ref = getattr(provider, "reference", None)
        if ref and pos.mark is None:
            symbol = eodhd_symbol(pos)
            q = (
                ref.quotes.get(pos.id)
                or (ref.quotes.get(symbol) if symbol else None)
            )
            if q:
                pos = pos.model_copy(
                    update={
                        "mark": Mark(
                            value=q.value,
                            as_of=q.as_of,
                            source=q.source,
                            captured_at=q.captured_at,
                            basis=q.basis,
                        )
                    }
                )

        return CandidateResult(position=pos, is_fund=(match.kind == "etf"))

    def market_data() -> FinancialProvider | None:
        try:
            return financial or PersonalFinancialProvider.from_environment()
        except (ValueError, OSError):
            return None

    def reference_identities() -> FinancialEvidence | None:
        return getattr(market_data(), "reference", None)

    @app.post("/api/analyze", response_model=AnalysisResult)
    async def analysis(request: AnalysisRequest) -> AnalysisResult:
        if request.artifact_id and request.artifact_reference is None:
            ref = portfolios.get_artifact_reference(request.artifact_id)
            if ref:
                request = request.model_copy(update={"artifact_reference": ref})
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
            # Stock Analysis, portfolio review, new cash, and confirmed theme read SEC EDGAR live
            # (cached) and issuer material; the reviewed file stays optional extra evidence.
            needs_research = (
                request.stock
                or request.portfolio_review
                or request.new_cash
                or (request.theme and request.theme.confirmed)
            )
            research_source = research or (
                IssuerResearchProvider.from_environment(SecResearchProvider.from_environment())
                if needs_research
                else None
            )
            return await analyze(
                request,
                provider,
                source,
                secret=config.api_key,
                financial=financial_source,
                research=research_source,
                discovery=discovery,
            )
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

            logging.error(
                "OpenAI request failed: type=%s status=%s request_id=%s",
                type(exc).__name__,
                getattr(exc, "status_code", None),
                getattr(exc, "request_id", None),
            )
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
            record = extract_decision(
                request.result,
                confirmed_action=request.confirmed_action,
                workflow=request.workflow,
                subject=request.subject,
            )
        elif request.decision is not None:
            record = request.decision
            updates: dict[str, Any] = {}
            if request.confirmed_action is not None:
                updates["confirmed_action"] = request.confirmed_action
            if request.workflow is not None:
                updates["workflow"] = request.workflow
            if request.subject is not None:
                updates["subject"] = request.subject
            if updates:
                record = record.model_copy(update=updates)
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

    # =========================================================================
    # Conversation & Run Endpoints (Ticket 03)
    # =========================================================================

    @app.post("/api/threads", response_model=ThreadSummary, status_code=201)
    def create_thread() -> ThreadSummary:
        return portfolios.db.create_thread()

    @app.get("/api/threads", response_model=list[ThreadSummary])
    def list_threads() -> list[ThreadSummary]:
        return portfolios.db.list_threads()

    @app.get("/api/threads/{thread_id}", response_model=ThreadDetail)
    def get_thread(thread_id: str) -> ThreadDetail:
        thread = portfolios.db.get_thread(thread_id)
        if thread is None:
            raise HTTPException(404, f"Thread '{thread_id}' not found.")
        return thread

    @app.post("/api/threads/{thread_id}/messages", response_model=RunRecord, status_code=202)
    async def submit_thread_message(thread_id: str, request: SubmitMessageRequest) -> RunRecord:
        # Bind revision once at acceptance: latest committed portfolio revision or None if absent
        current_rev = portfolios.get_revisioned()
        bound_revision = current_rev.revision if current_rev else None

        run_record, is_new = portfolios.db.submit_message(
            thread_id=thread_id,
            request=request,
            bound_revision=bound_revision,
        )

        if is_new:
            get_orchestrator().submit_supervised_run(
                thread_id=thread_id,
                run_record=run_record,
                user_text=request.text,
                workflow_input=request.workflow_input,
                hypothetical_artifact_id=request.hypothetical_artifact_id,
            )

        return run_record

    @app.get("/api/threads/{thread_id}/runs/{run_id}", response_model=RunRecord)
    def get_thread_run(thread_id: str, run_id: str) -> RunRecord:
        # Cross-thread lookups must return 404
        run_record = portfolios.db.get_run(thread_id, run_id)
        if run_record is None:
            raise HTTPException(404, f"Run '{run_id}' not found in thread '{thread_id}'.")
        return run_record

    @app.post("/api/threads/{thread_id}/runs/{run_id}/cancel", response_model=RunRecord)
    async def cancel_thread_run(thread_id: str, run_id: str) -> RunRecord:
        run_record = await get_orchestrator().cancel_run(thread_id, run_id)
        if run_record is None:
            raise HTTPException(404, f"Run '{run_id}' not found in thread '{thread_id}'.")
        return run_record

    return app


app = create_app()
