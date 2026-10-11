# 03: Immutable preparation and proposal retrieval

**What to build:** Convert import, PUT, and identify into proposal preparation and expose pending previews.

**Blocked by:** 02: Legacy portfolio migration and revisioned reads.

**Status:** closed

**Authority:** [Portfolio Actions and Persistence specification](../../../specs/portfolio-actions-and-persistence.md).

## Scope and files affected

- `backend/analyst/portfolio.py`: reuse parsing, identify, and enrichment services without committing.
- New `backend/analyst/portfolio_actions.py`: one shared preparation service, differences, warnings, and no-op handling.
- `backend/analyst/api.py`, `backend/analyst/schemas.py`, and persistence module: preparation and proposal list/detail contracts.
- `backend/tests/test_portfolio_context.py`, `backend/tests/test_enrichment.py`, and proposal API tests.

Recheck the base revision after external preparation work and before persisting the proposal. Store immutable before/after artifacts and validated results. Import replaces holdings/costs but preserves committed rules and benchmark. Reuse guardrail calculations as warnings without relaxing investment sizing restrictions.

## Acceptance criteria

- [x] All three preparation routes require a base revision and never commit.
- [x] Existing parsers, correction logic, and enrichment are reused.
- [x] Proposals persist exact before/after content, differences, and warnings.
- [x] No-op requests return `unchanged`.
- [x] Pending proposals can be listed/reopened after restart.
- [x] Import replacement preserves committed rules and benchmark.
- [x] No immediate-write route remains.

## Tests and verification

- API preparation for first creation, replacement, manual edits, and partial identity correction.
- Stale bases, invalid input, and no-op detection.
- Guardrail warnings, unknown-input qualifications, and unchanged committed revision/content.
- Retrieval and restart restoration of exact immutable previews.

## Integration requirements

Stay on the coordinated integration branch. Spec 1 will use this same preparation service with optional thread/run identifiers; no separate agent mutation path.
