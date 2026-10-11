# 04: Atomic confirmation, cancellation, and receipts

**What to build:** Explicitly apply or cancel a prepared proposal with safe retries and concurrency.

**Blocked by:** 03: Immutable preparation and proposal retrieval.

**Status:** completed

**Authority:** [Portfolio Actions and Persistence specification](../../../specs/portfolio-actions-and-persistence.md).

## Scope and files affected

- `backend/analyst/portfolio_actions.py` and `backend/analyst/persistence.py`: atomic commit, cancellation, stale handling, receipts, and retry behavior.
- `backend/analyst/api.py`: confirmation/cancellation endpoints with empty requests.
- Portfolio fixtures and action API/storage tests.

Commit the revision, current pointer, proposal status, receipt, and competing stale proposals in one transaction. Do not rerun enrichment during confirmation. Preserve the approved semantics for duplicate confirmation and cancellation; sanitize failure responses.

## Acceptance criteria

- [x] Confirmation commits exactly the stored payload and creates one receipt.
- [x] Duplicate confirmation returns the same receipt.
- [x] Competing confirmations cannot overwrite newer state.
- [x] Cancelled/stale proposals cannot apply.
- [x] Faults roll back revision, current pointer, status, and receipt together.
- [x] Applied confirmation is independent of current provider availability.


## Tests and verification

- API lifecycle and restart tests.
- Concurrent confirmations through separate connections.
- Commit fault injection and retryable locking failure behavior.
- Rejection of replacement payloads and duplicate cancellation behavior.
- Applied confirmation retries after later revisions exist.

## Integration requirements

Only the dedicated user confirmation control may invoke commitment; conversation agreement and decision-journal confirmations remain separate. Stay on the coordinated integration branch.
