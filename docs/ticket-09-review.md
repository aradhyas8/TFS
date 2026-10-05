# Ticket 09 completion and review report

Reviewed October 4, 2026. Baseline: `ticket-08-theme-discovery`. The implement workflow used the `ticket-09-etf-indirect-overlap-look-through` branch and verified all unit, boundary, integration, and browser seams.

## Delivered behavior

1. **ETF Sponsor-Holdings Access & Tool Support:**
   - Extended `FinancialProvider` with `sponsor_holdings(ticker, listing, as_of)`.
   - Loaded reference holdings from `SPONSOR_HOLDINGS_REFERENCE_FILE` in `PersonalFinancialProvider.from_environment()`.
   - Exposed `get_sponsor_holdings` as an authorized tool within the pipeline whitelist.
   - Enforced snapshot date matching and capture freshness: outdated holdings or contradictory capture dates are marked `stale`.

2. **Deterministic Look-Through & Aggregation:**
   - Added recursive look-through for nested funds with cycle prevention and depth control.
   - Aggregated direct and indirect exposures per company without double counting (fund position values are retained in the portfolio total; underlying holdings decompose indirect exposure).
   - Honestly labeled portfolio and company look-through coverage: `full`, `partial`, `stale`, `unknown`, `none`.

3. **Explicit Indirect Cap Policy:**
   - Enforced `indirect_cap_policy` under `settings`:
     - `direct_only`: Direct stock weight is checked against `single_company_cap`; indirect exposure is reported and explained without triggering a cap breach.
     - `include_known_indirect`: Direct + known indirect weight is evaluated against `single_company_cap`.
   - Partial look-through semantics: Known exposure within limits yields `within_limit` qualified with an explanatory disclosure that unlisted holdings could contain additional exposure. It does not block numeric sizing.

4. **Sizing & Portfolio Re-Underwriting Integration:**
   - Updated `size_allocation` and `size_review` to incorporate existing indirect exposure into company value calculations when `settings.indirect_cap_policy == "include_known_indirect"`.
   - Ensured `preview_changes` evaluates post-allocation caps with direct + indirect weights under `include_known_indirect`.

5. **Frontend UI & Presentation:**
   - Added an "ETF indirect overlap & look-through" table in `ReviewResult.tsx` displaying direct, indirect, and total values and weights, honest coverage badges, and contributing funds with individual fund weights, indirect values, source labels, and dates.
   - Enhanced `GuardrailResult.tsx` to display direct and indirect weight breakdowns and explicit policy labels.

## Verification

| Check | Result |
| --- | --- |
| Backend pytest suite | 302 passed (all tests in `tests/test_etf_look_through.py` and existing suite) |
| Backend mypy strict | Passed (0 issues in 15 source files) |
| Backend Ruff linter | Passed (All checks passed) |
| Frontend TypeScript (`tsc --noEmit`) | Passed (0 errors) |
| Frontend Playwright suite | 30 passed (all 4 new tests in `tests/look_through.spec.ts` and 26 existing tests) |
| Next.js production build (`npm run build`) | Passed (Compiled successfully, static pages generated) |

No live network calls were made to OpenAI or external market data.

## Acceptance criteria status

- [x] Known company overlap and its source dates appear in the existing reviews and recommendations.
- [x] Partial, unknown, nested, and stale coverage is represented honestly.
- [x] Known indirect exposure affects configured cap checks only according to the user's explicit policy.
- [x] Direct blocker remains ticket 03, and no core workflow gains a look-through dependency.
