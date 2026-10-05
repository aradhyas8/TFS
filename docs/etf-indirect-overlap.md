# ETF indirect overlap and recursive look-through

Ticket 09 improves portfolio reviews and recommendations by looking through exchange-traded funds (ETFs) to aggregate direct and indirect company exposures using dated sponsor holdings, with support for recursive nested fund look-through and honest coverage labeling.

## Overview

Portfolios frequently hold both direct company equities (e.g., individual shares of Acme Corp) and diversified or sector/theme ETFs that also hold those same companies. Without look-through, an investor may have hidden concentration risk. Ticket 09 surfaces this overlap and enforces the user's explicit policy for company caps without double counting fund position values or inventing complete coverage when only partial data exists.

## Core architecture and design

```
                       ┌──────────────────────────────┐
                       │   Portfolio Snapshot (CSV)   │
                       └──────────────┬───────────────┘
                                      │
                                      ▼
                        ┌───────────────────────────┐
                        │ Financial Data Provider   │
                        │ (PersonalFinancialProvider│
                        │  / FakeFinancialProvider) │
                        └─────────────┬─────────────┘
                                      │
                     Query sponsor_holdings for ETFs
                     Validate snapshot dates & freshness
                                      │
                                      ▼
                      ┌───────────────────────────────┐
                      │ Deterministic Calculations    │
                      │ (analyst/calculations.py)     │
                      └───────────────┬───────────────┘
                                      │
                 Recursive Look-Through & Cycle Prevention
                 Decompose indirect exposure per company
                 Label portfolio & company coverage honestly
                                      │
                                      ▼
                      ┌───────────────────────────────┐
                      │ Guardrail Enforcement         │
                      │ (analyst/guardrails.py)       │
                      └───────────────┬───────────────┘
                                      │
                 Evaluate explicit indirect_cap_policy:
                 - direct_only (direct weight vs cap)
                 - include_known_indirect (direct+indirect vs cap)
                 Partial coverage qualifies, doesn't block
                                      │
                                      ▼
                      ┌───────────────────────────────┐
                      │ Structured Result & Frontend  │
                      │ (ReviewResult & Guardrails)   │
                      └───────────────────────────────┘
```

### 1. Sponsor holdings and nested look-through
- **Provider seam:** `FinancialProvider.sponsor_holdings(ticker, listing, as_of)` retrieves dated sponsor holdings.
- **Environment and reference file:** Configurable via `SPONSOR_HOLDINGS_REFERENCE_FILE` pointing to structured JSON holdings.
- **Recursive constituent resolution:** Underlying fund holdings are resolved recursively up to depth limits while preventing cycles using visited identifier sets.
- **Date matching & freshness:** Holdings files whose `as_of` date does not match the portfolio snapshot date, or which have been marked stale during capture, are marked `stale` and not treated as current.

### 2. Zero double counting
- Portfolio total value sums direct stock values + ETF position values + cash.
- ETF constituents only decompose indirect exposure; they are **never** added to the portfolio total value or `review.positions`.
- Total company value is `direct_value + indirect_value`. Direct weights and indirect weights sum to total company weight.

### 3. Honest coverage labeling
Incomplete look-through is never assumed to be zero:
- `full`: Full sponsor holdings file dated as of the portfolio snapshot date.
- `partial`: Top-10 or partial constituent lists. Discloses that unlisted holdings could contain additional unquantified exposure.
- `stale`: Sponsor holdings dated differently from the snapshot date.
- `unknown`: No sponsor holdings found or unverified identity/mark.
- `none`: No ETFs held in the portfolio.

### 4. Explicit indirect cap policy
The user configures `indirect_cap_policy` under `settings`:
- `direct_only`: Only direct stock exposure is evaluated against `single_company_cap`. Known indirect overlap is noted in the explanation and displayed in the table, but does not trigger a cap breach.
- `include_known_indirect`: Direct exposure plus known indirect ETF overlap is evaluated against `single_company_cap`. If the combined weight exceeds the cap, it triggers a `breached` status and calculates required reduction to cash.
- **Partial look-through semantics:** When `include_known_indirect` is selected and look-through is `partial`, if known exposure is within the limit, the status evaluates to `within_limit` qualified with an explanatory note. It does not block valid sizing. If known exposure already exceeds the limit, it evaluates to `breached`. If coverage is `unknown` or `stale`, status evaluates to `unknown`.

### 5. Sizing integration
In `size_allocation` (`analyst/allocation.py`) and `size_review` (`analyst/reunderwriting.py`):
- When `settings.indirect_cap_policy == "include_known_indirect"`, existing company exposure includes known indirect exposure from ETF overlap.
- Sizing to a target weight targets the *total* company exposure, calculating the necessary direct addition or reduction accordingly.
- `preview_changes` confirms post-transaction portfolio compliance under the chosen policy.

### 6. User interface
- **Review result table:** Displays "ETF indirect overlap & look-through" with direct, indirect, and total values and weights, honest coverage badges, contributing funds with individual weights and indirect dollar amounts, and source dates.
- **Guardrail result:** Displays the active policy (`Include known indirect ETF overlap` vs `Direct stock only`), breakdown of Direct vs Indirect weights, and honest qualification explanations.
