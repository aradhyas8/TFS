# 09: Integrated cutover, migration recovery, and regression acceptance

**What to build:** Deliver the coordinated frontend/backend change with no remaining write bypasses.

**Blocked by:** 06: First import and replacement-import previews; 07: Identity-correction previews and pending-action recovery; 08: Immutable hypothetical draft analysis.

**Status:** ready-for-agent

**Authority:** [Portfolio Actions and Persistence specification](../../../specs/portfolio-actions-and-persistence.md).

## Scope and files affected

- `backend/tests/conftest.py`, `backend/tests/e2e_server.py`, and shared API fixtures.
- Existing backend API/storage/workflow and frontend browser suites.
- `README.md` and relevant configuration documentation: migration, database backups/recovery, rollback limits, and coordinated deployment.
- Specification/tracker authority references as needed to identify this approved scope without replacing unrelated historical ticket authority.

Audit every production portfolio writer. Remove assumptions that imports or PUTs commit immediately. Preserve existing financial providers, calculations, workflow gates, temporary-row rules, and decision journaling.

## Acceptance criteria

- [x] Every production portfolio writer uses explicit confirmation.
- [x] All setup fixtures use preparation/confirmation rather than hidden saves.
- [x] Existing workflows, candidate lookup, price refresh, and decision journal retain their approved behavior.
- [x] Database upgrade backups and recovery instructions are verified.
- [x] Restart preserves committed revisions, pending proposals, and receipts.
- [x] Spec 1 can consume shared contracts and the preparation service without a second action implementation.

## Tests and verification

- Full backend pytest, mypy, and ruff.
- Frontend typecheck, browser suite, and production build.
- Isolated migration/restart and recovery scenarios.
- All automated tests use fake providers and no live model/market-data calls.

## Integration requirements

Merge/deploy the coordinated integration branch only when this ticket passes. Ticket 01 may already have landed independently; tickets 02–09 must not be deployed as partial cutovers. No compatibility switches, dual writes, or immediate-write bypasses. Record that rolling back to the JSON-only application after SQLite commits exposes stale state.
