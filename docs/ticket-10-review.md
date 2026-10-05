# Ticket 10 completion and review report

Reviewed October 5, 2026. Baseline: `ticket-09-etf-indirect-overlap-look-through`. The implement workflow used the `ticket-10-canadian-stock-evidence-support` branch and verified all unit, boundary, integration, and browser seams.

## Delivered behavior

1. **Canadian Listings & Identity Handling:**
   - Added `CANADIAN_LISTINGS = frozenset({"XTSE", "XTSX", "NEOE", "XCNQ"})` and `is_canadian_security(currency, listing)` in `schemas.py`.
   - Updated `is_supported_stock` to accept Canadian stocks (`CAD` currency on Canadian listings) alongside US stocks (`USD` on `XNAS`, `XNYS`, `XASE`).
   - Extended `ResearchDocument`: allows `authority: Literal["sec", "sedar", "sedar_plus", "issuer", "macro"]` and enforces that SEDAR+ links point to exact non-root paths on `www.sedarplus.ca` or `sedarplus.ca`.

2. **Research Sourcing & SEDAR+ Tool Integration:**
   - Added `get_sedar_filings` tool in `providers.py` and whitelisted it in `pipeline.py`.
   - Enforced filing tool dispatch: Canadian stock analysis expects `get_sedar_filings`; calling `get_sec_filings` on a Canadian stock (or `get_sedar_filings` on a US stock) is rejected with HTTP 502.
   - Updated `ReviewedResearchProvider` in `research.py` to support listing-qualified (`company_id:listing`) and currency-qualified company lookups, allowing dual-listed or Canadian-specific issuer filings without collision.

3. **Deterministic Company Valuations & Pipeline Validation:**
   - In `stock.py`, `calculate_company_cases` dynamically resolves `required_filing = {"sedar", "sedar_plus"}` for Canadian stocks and verifies CAD reported facts and diluted shares against primary checks.
   - Missing or conflicting Canadian evidence leaves cases unresolved (`terminal_price: null`), compelling `wait_for_inputs`.
   - In `pipeline.py`, `validate_stock_recommendation` checks target position currency and listing to verify required SEDAR+ citations.
   - Hardened `validate_prose` to unconditionally reject claims of automated SEDAR+ scraping, SEDAR+ database construction, and fabricated tax exemptions.

4. **Multi-Workflow Availability:**
   - **New Cash Allocation:** `ReviewedDiscoveryProvider.scan` and `DiscoveryCandidate.prospective` permit Canadian discovery candidates.
   - **Portfolio Review:** `bind_holdings` in `reunderwriting.py` binds held Canadian equities for thesis re-underwriting and comparison.
   - **Theme Exploration:** `AnalysisRequest` shortlist and theme comparison rules support Canadian candidates.

5. **Frontend UI & Verification Links:**
   - Updated `contracts.ts`: exported `US_LISTINGS`, `CANADIAN_LISTINGS`, and `isSupportedStock`; updated `portfolioReviewComparison` and `isThemeCandidate`.
   - Updated `page.tsx`: stock dropdown displays Canadian stocks (`XTSE`, `XTSX`, `NEOE`, `XCNQ`) alongside US stocks; preserves selection state across snapshot updates.
   - Updated `StockResult.tsx`: displays distinct `SEDAR+ verification link` badge and explicit note instructing user to open the exact SEDAR+ link directly.

## Verification

| Check | Result |
| --- | --- |
| Backend pytest suite | 318 passed (all 16 tests in `tests/test_canadian_stock.py` and 302 existing tests) |
| Backend mypy strict | Passed (0 issues in 15 source files) |
| Frontend TypeScript (`tsc --noEmit`) | Passed (0 errors) |
| Frontend Next.js build (`npm run build`) | Passed (Compiled successfully, static pages generated) |
| Frontend Playwright suite | 32 passed (both tests in `tests/canadian_stock.spec.ts` and 30 existing tests) |

No live network calls were made to OpenAI, SEDAR+, or external market data services.

## Acceptance criteria status

- [x] A Canadian stock can be assessed through the same pipeline using permitted evidence and exact SEDAR+ verification links.
- [x] Evidence limits and unresolved facts remain visible and affect confidence/sizing appropriately.
- [x] Existing US and portfolio journeys remain usable and unchanged in architecture.
- [x] Only ticket 05 directly blocks this extension; priority remains after the main four workflows.
