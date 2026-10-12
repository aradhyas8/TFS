"""SQLite persistence foundation for portfolio revisions, artifacts, and proposals."""

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Generator, Literal

from .schemas import (
    ArtifactEnvelope,
    ArtifactReference,
    DifferenceItem,
    MessageRecord,
    MessageRole,
    PortfolioProposal,
    PortfolioProposalDetail,
    ProposalOrigin,
    ProposalReceipt,
    ProposalStatus,
    RevisionedPortfolio,
    RunRecord,
    RunStatus,
    SavedPortfolio,
    SubmitMessageRequest,
    ThreadDetail,
    ThreadSummary,
    ToolOutcome,
    build_artifact_payload,
)

SCHEMA_VERSION = 2


class SchemaMigrationError(RuntimeError):
    """Raised when database schema version is incompatible."""


class LegacyMigrationError(RuntimeError):
    """Raised when legacy JSON portfolio cannot be parsed or validated."""


class StaleBaseRevisionError(RuntimeError):
    """Raised when base revision does not match current committed revision."""


class ProposalNotFoundError(RuntimeError):
    """Raised when a proposal ID is not found."""


class InvalidProposalStateError(RuntimeError):
    """Raised when a proposal state transition is invalid or conflicting."""


class DatabaseLockError(RuntimeError):
    """Raised when SQLite operations fail due to a lock or busy condition."""


class ThreadNotFoundError(RuntimeError):
    """Raised when a thread ID is not found."""


class RunConflictError(RuntimeError):
    """Raised when a run payload conflicts with an existing client request ID or another run is active."""





def default_database_path() -> Path:
    portfolio_dir = os.environ.get("PORTFOLIO_DIR")
    if portfolio_dir:
        return Path(portfolio_dir) / "portfolio.db"
    return Path(__file__).resolve().parents[1] / "data" / "portfolio" / "portfolio.db"


class DecimalEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return super().default(o)



class Database:
    """Manages SQLite connection lifecycle, schema initialization, and immutable artifact storage."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_database_path()

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(self.path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            try:
                yield conn
                conn.commit()
            except sqlite3.OperationalError as exc:
                conn.rollback()
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    raise DatabaseLockError(f"Database is locked or busy: {exc}") from exc
                raise
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise DatabaseLockError(f"Database is locked or busy: {exc}") from exc
            raise


    def get_schema_version(self) -> int:
        if not self.path.exists():
            return 0
        with self.connect() as conn:
            cursor = conn.execute("PRAGMA user_version;")
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def initialize(self) -> None:
        """Initialize database schema idempotently with forward migrations and automated backup."""
        with self.connect() as conn:
            cursor = conn.execute("PRAGMA user_version;")
            current_version = int(cursor.fetchone()[0])
            if current_version == 0:
                self._apply_initial_schema(conn)
                self._apply_migration_v2(conn)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
            elif current_version == 1:
                # Backup database before forward migration
                self._backup_database(conn)
                self._apply_migration_v2(conn)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
            elif current_version > SCHEMA_VERSION:
                raise SchemaMigrationError(
                    f"Database schema version {current_version} is higher than supported {SCHEMA_VERSION}."
                )

    def _backup_database(self, source_conn: sqlite3.Connection) -> Path:
        """Create an atomic backup using SQLite backup API before forward migration."""
        backup_path = self.path.with_name(f"{self.path.stem}.backup{self.path.suffix}")
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_conn = sqlite3.connect(backup_path)
        try:
            source_conn.backup(backup_conn)
        finally:
            backup_conn.close()
        return backup_path

    def _apply_initial_schema(self, conn: sqlite3.Connection) -> None:
        # Schema metadata / migration tracker
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
        """)

        # Revisions table for committed portfolios
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_revisions (
                revision INTEGER PRIMARY KEY CHECK(revision > 0),
                committed_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        # Current portfolio pointer
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_current (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                current_revision INTEGER,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (current_revision) REFERENCES portfolio_revisions(revision)
            );
        """)

        # Immutable artifacts table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                label TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revision INTEGER,
                basis TEXT NOT NULL,
                source_artifact_id TEXT,
                originating_run_id TEXT,
                payload_json TEXT NOT NULL
            );
        """)

        # Action proposals table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY,
                base_revision INTEGER,
                origin TEXT NOT NULL,
                thread_id TEXT,
                run_id TEXT,
                before_artifact_id TEXT,
                after_artifact_id TEXT NOT NULL,
                differences_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'applied', 'cancelled', 'stale')),
                created_at TEXT NOT NULL,
                FOREIGN KEY (before_artifact_id) REFERENCES artifacts(id),
                FOREIGN KEY (after_artifact_id) REFERENCES artifacts(id)
            );
        """)

        # Action proposal receipts table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS proposal_receipts (
                proposal_id TEXT PRIMARY KEY,
                previous_revision INTEGER,
                new_revision INTEGER NOT NULL UNIQUE,
                committed_at TEXT NOT NULL,
                FOREIGN KEY (proposal_id) REFERENCES proposals(id),
                FOREIGN KEY (new_revision) REFERENCES portfolio_revisions(revision)
            );
        """)

        # Migration metadata table (used by Ticket 02 to record legacy migration status)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (1, ?);",
            (datetime.now(UTC).isoformat(),),
        )

    def _apply_migration_v2(self, conn: sqlite3.Connection) -> None:
        """Apply migration v2: conversation tables (threads, messages, runs, private_continuation)."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                client_request_id TEXT NOT NULL,
                request_payload_hash TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed', 'cancelled', 'interrupted')),
                bound_revision INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error TEXT,
                message_ids_json TEXT NOT NULL,
                artifact_ids_json TEXT NOT NULL,
                FOREIGN KEY (thread_id) REFERENCES threads(id),
                UNIQUE(thread_id, client_request_id)
            );
        """)

        # At most one running run per thread at database level
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_single_running
            ON runs(thread_id)
            WHERE status = 'running';
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                run_id TEXT,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                text TEXT NOT NULL,
                citations_json TEXT NOT NULL,
                artifact_ids_json TEXT NOT NULL,
                clarification_required_json TEXT,
                proposal_ids_json TEXT NOT NULL,
                tool_outcomes_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (thread_id) REFERENCES threads(id),
                FOREIGN KEY (run_id) REFERENCES runs(id)
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS private_continuation (
                run_id TEXT PRIMARY KEY,
                continuation_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(id)
            );
        """)

        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (2, ?);",
            (datetime.now(UTC).isoformat(),),
        )

    def store_artifact(
        self,
        reference: ArtifactReference,
        payload: Any,
    ) -> None:
        """Store immutable artifact payload and metadata."""
        raw_payload = build_artifact_payload(payload, kind=reference.kind, projection="human")
        payload_json = json.dumps(raw_payload, cls=DecimalEncoder)

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (
                    id, kind, label, created_at, revision, basis,
                    source_artifact_id, originating_run_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    reference.id,
                    reference.kind,
                    reference.label,
                    reference.created_at.isoformat(),
                    reference.revision,
                    reference.basis,
                    reference.source_artifact_id,
                    reference.originating_run_id,
                    payload_json,
                ),
            )

    def store_hypothetical_artifact(
        self,
        portfolio: SavedPortfolio,
        label: str = "Hypothetical Draft Portfolio",
        source_artifact_id: str | None = None,
        originating_run_id: str | None = None,
    ) -> ArtifactReference:
        """Store immutable hypothetical draft portfolio artifact."""
        now = datetime.now(UTC)
        artifact_id = f"art-hypo-{uuid.uuid4().hex[:12]}"
        if source_artifact_id is None:
            current = self.get_current_portfolio()
            if current:
                with self.connect() as conn:
                    cursor = conn.execute(
                        "SELECT id FROM artifacts WHERE revision = ? AND basis = 'committed' ORDER BY created_at DESC LIMIT 1;",
                        (current.revision,),
                    )
                    row = cursor.fetchone()
                    if row:
                        source_artifact_id = row["id"]
        ref = ArtifactReference(
            id=artifact_id,
            kind="portfolio_snapshot",
            label=label,
            created_at=now,
            revision=None,
            basis="hypothetical",
            source_artifact_id=source_artifact_id,
            originating_run_id=originating_run_id,
        )
        self.store_artifact(ref, portfolio)
        return ref

    def get_artifact(
        self,
        artifact_id: str,
        projection: Literal["human", "model"] = "human",
    ) -> Any | None:
        """Retrieve stored artifact payload with the chosen projection."""
        with self.connect() as conn:
            cursor = conn.execute(
                "SELECT kind, payload_json FROM artifacts WHERE id = ?;",
                (artifact_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            data = json.loads(row["payload_json"])
            kind = row["kind"]

            return build_artifact_payload(data, kind=kind, projection=projection)

    def get_artifact_envelope(
        self,
        artifact_id: str,
        projection: Literal["human", "model"] = "human",
    ) -> ArtifactEnvelope | None:
        """Retrieve stored artifact envelope containing reference and projected payload."""
        ref = self.get_artifact_reference(artifact_id)
        if not ref:
            return None
        payload = self.get_artifact(artifact_id, projection=projection)
        if payload is None:
            return None
        return ArtifactEnvelope(reference=ref, payload=payload)

    def get_artifact_reference(self, artifact_id: str) -> ArtifactReference | None:
        """Retrieve artifact reference metadata without the full payload."""
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT id, kind, label, created_at, revision, basis, source_artifact_id, originating_run_id
                FROM artifacts WHERE id = ?;
                """,
                (artifact_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return ArtifactReference(
                id=row["id"],
                kind=row["kind"],
                label=row["label"],
                created_at=datetime.fromisoformat(row["created_at"]),
                revision=row["revision"],
                basis=row["basis"],
                source_artifact_id=row["source_artifact_id"],
                originating_run_id=row["originating_run_id"],
            )

    def is_legacy_migration_complete(self) -> bool:
        """Check if legacy migration has already run."""
        with self.connect() as conn:
            cursor = conn.execute(
                "SELECT value FROM metadata WHERE key = 'legacy_migration';"
            )
            return cursor.fetchone() is not None

    def migrate_legacy_portfolio(self, legacy_json_path: Path | str | None) -> None:
        """Import legacy current.json into revision 1 without enrichment, atomically."""
        path = Path(legacy_json_path) if legacy_json_path else None
        with self.connect() as conn:
            # Check if migration has already been recorded
            cursor = conn.execute(
                "SELECT value FROM metadata WHERE key = 'legacy_migration';"
            )
            if cursor.fetchone() is not None:
                return

            now_iso = datetime.now(UTC).isoformat()
            if path is None or not path.exists():
                conn.execute(
                    "INSERT INTO metadata (key, value, updated_at) VALUES ('legacy_migration', 'no_legacy_data', ?);",
                    (now_iso,),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO portfolio_current (id, current_revision, updated_at) VALUES (1, NULL, ?);",
                    (now_iso,),
                )
                return

            try:
                content = path.read_text(encoding="utf-8")
                portfolio = SavedPortfolio.model_validate_json(content)
            except Exception as exc:
                raise LegacyMigrationError(f"Failed to migrate legacy portfolio JSON: {exc}") from exc

            payload_json = json.dumps(portfolio.model_dump(mode="python"), cls=DecimalEncoder)
            committed_at = (portfolio.saved_at or datetime.now(UTC)).isoformat()

            conn.execute(
                "INSERT OR IGNORE INTO portfolio_revisions (revision, committed_at, payload) VALUES (1, ?, ?);",
                (committed_at, payload_json),
            )
            conn.execute(
                """
                INSERT INTO portfolio_current (id, current_revision, updated_at)
                VALUES (1, 1, ?)
                ON CONFLICT(id) DO UPDATE SET current_revision = 1, updated_at = excluded.updated_at;
                """,
                (now_iso,),
            )
            conn.execute(
                "INSERT OR IGNORE INTO metadata (key, value, updated_at) VALUES ('legacy_migration', 'completed', ?);",
                (now_iso,),
            )

    def get_current_portfolio(self) -> RevisionedPortfolio | None:
        """Get the current committed portfolio with its revision without enrichment or writes."""
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT r.revision, r.committed_at, r.payload
                FROM portfolio_current c
                JOIN portfolio_revisions r ON c.current_revision = r.revision
                WHERE c.id = 1;
                """
            )
            row = cursor.fetchone()
            if not row:
                return None
            data = json.loads(row["payload"])
            portfolio = SavedPortfolio.model_validate(data)
            return RevisionedPortfolio(
                revision=row["revision"],
                portfolio=portfolio,
                committed_at=datetime.fromisoformat(row["committed_at"]),
            )

    def get_portfolio_revision(self, revision: int) -> RevisionedPortfolio | None:
        """Read an immutable run basis without consulting the current pointer."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT revision, committed_at, payload FROM portfolio_revisions WHERE revision = ?;",
                (revision,),
            ).fetchone()
            if row is None:
                return None
            return RevisionedPortfolio(
                revision=row["revision"], committed_at=datetime.fromisoformat(row["committed_at"]),
                portfolio=SavedPortfolio.model_validate_json(row["payload"]),
            )

    def save_revision(self, portfolio: SavedPortfolio) -> RevisionedPortfolio:
        """Save a new committed revision and advance the current pointer atomically."""
        now = datetime.now(UTC)
        committed_at_iso = now.isoformat()
        portfolio_with_saved_at = portfolio.model_copy(update={"saved_at": now})
        payload_json = json.dumps(portfolio_with_saved_at.model_dump(mode="python"), cls=DecimalEncoder)

        with self.connect() as conn:
            cursor = conn.execute("SELECT MAX(revision) FROM portfolio_revisions;")
            row = cursor.fetchone()
            next_rev = 1 if (not row or row[0] is None) else (row[0] + 1)

            conn.execute(
                "INSERT INTO portfolio_revisions (revision, committed_at, payload) VALUES (?, ?, ?);",
                (next_rev, committed_at_iso, payload_json),
            )
            conn.execute(
                """
                INSERT INTO portfolio_current (id, current_revision, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET current_revision = excluded.current_revision, updated_at = excluded.updated_at;
                """,
                (next_rev, committed_at_iso),
            )
            art_id = f"art-rev-{next_rev}"
            conn.execute(
                """
                INSERT OR REPLACE INTO artifacts (
                    id, kind, label, created_at, revision, basis,
                    source_artifact_id, originating_run_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    art_id,
                    "portfolio_snapshot",
                    f"Committed Portfolio (rev {next_rev})",
                    committed_at_iso,
                    next_rev,
                    "committed",
                    None,
                    None,
                    json.dumps(build_artifact_payload(portfolio_with_saved_at, projection="human"), cls=DecimalEncoder),
                ),
            )
            return RevisionedPortfolio(
                revision=next_rev,
                portfolio=portfolio_with_saved_at,
                committed_at=now,
            )

    def _row_to_proposal(self, row: sqlite3.Row) -> PortfolioProposal:
        before_ref = self.get_artifact_reference(row["before_artifact_id"]) if row["before_artifact_id"] else None
        after_ref = self.get_artifact_reference(row["after_artifact_id"])
        if after_ref is None:
            raise RuntimeError(f"Missing after artifact {row['after_artifact_id']} for proposal {row['id']}")

        diffs_raw = json.loads(row["differences_json"])
        differences = [DifferenceItem.model_validate(d) for d in diffs_raw]
        warnings = json.loads(row["warnings_json"])

        receipt = None
        if row["new_revision"] is not None:
            receipt = ProposalReceipt(
                proposal_id=row["id"],
                previous_revision=row["previous_revision"],
                new_revision=row["new_revision"],
                committed_at=datetime.fromisoformat(row["receipt_committed_at"]),
            )

        return PortfolioProposal(
            id=row["id"],
            base_revision=row["base_revision"],
            origin=row["origin"],
            thread_id=row["thread_id"],
            run_id=row["run_id"],
            before_artifact=before_ref,
            after_artifact=after_ref,
            differences=differences,
            warnings=warnings,
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            receipt=receipt,
        )

    def store_proposal(
        self,
        base_revision: int | None,
        origin: ProposalOrigin,
        before_portfolio: SavedPortfolio | None,
        after_portfolio: SavedPortfolio,
        differences: list[DifferenceItem],
        warnings: list[str],
        thread_id: str | None = None,
        run_id: str | None = None,
    ) -> PortfolioProposal:
        """Store immutable before/after artifacts and validated proposal, rechecking base revision atomically."""
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        proposal_id = f"prop-{uuid.uuid4().hex[:12]}"

        with self.connect() as conn:
            cursor = conn.execute("SELECT current_revision FROM portfolio_current WHERE id = 1;")
            row = cursor.fetchone()
            current_rev = row["current_revision"] if row else None
            if current_rev != base_revision:
                raise StaleBaseRevisionError(
                    f"Base revision {base_revision} is stale; current revision is {current_rev}."
                )

            before_artifact_id: str | None = None
            before_ref: ArtifactReference | None = None
            if before_portfolio is not None:
                before_artifact_id = f"art-before-{proposal_id}"
                before_ref = ArtifactReference(
                    id=before_artifact_id,
                    kind="portfolio_snapshot",
                    label=f"Committed Portfolio (Rev {base_revision})",
                    created_at=now,
                    revision=base_revision,
                    basis="committed",
                )
                payload_json = json.dumps(
                    build_artifact_payload(before_portfolio, projection="human"),
                    cls=DecimalEncoder,
                )
                conn.execute(
                    """
                    INSERT INTO artifacts (
                        id, kind, label, created_at, revision, basis,
                        source_artifact_id, originating_run_id, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        before_ref.id,
                        before_ref.kind,
                        before_ref.label,
                        before_ref.created_at.isoformat(),
                        before_ref.revision,
                        before_ref.basis,
                        None,
                        None,
                        payload_json,
                    ),
                )

            after_artifact_id = f"art-after-{proposal_id}"
            after_ref = ArtifactReference(
                id=after_artifact_id,
                kind="portfolio_snapshot",
                label=f"Prepared Portfolio ({origin})",
                created_at=now,
                revision=None,
                basis="hypothetical",
                source_artifact_id=before_artifact_id,
                originating_run_id=run_id,
            )
            payload_json = json.dumps(
                build_artifact_payload(after_portfolio, projection="human"),
                cls=DecimalEncoder,
            )
            conn.execute(
                """
                INSERT INTO artifacts (
                    id, kind, label, created_at, revision, basis,
                    source_artifact_id, originating_run_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    after_ref.id,
                    after_ref.kind,
                    after_ref.label,
                    after_ref.created_at.isoformat(),
                    after_ref.revision,
                    after_ref.basis,
                    after_ref.source_artifact_id,
                    after_ref.originating_run_id,
                    payload_json,
                ),
            )

            differences_json = json.dumps(
                [d.model_dump(mode="python") for d in differences],
                cls=DecimalEncoder,
            )
            warnings_json = json.dumps(warnings)

            conn.execute(
                """
                INSERT INTO proposals (
                    id, base_revision, origin, thread_id, run_id,
                    before_artifact_id, after_artifact_id, differences_json,
                    warnings_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?);
                """,
                (
                    proposal_id,
                    base_revision,
                    origin,
                    thread_id,
                    run_id,
                    before_artifact_id,
                    after_artifact_id,
                    differences_json,
                    warnings_json,
                    now_iso,
                ),
            )

            return PortfolioProposal(
                id=proposal_id,
                base_revision=base_revision,
                origin=origin,
                thread_id=thread_id,
                run_id=run_id,
                before_artifact=before_ref,
                after_artifact=after_ref,
                differences=differences,
                warnings=warnings,
                status="pending",
                created_at=now,
                receipt=None,
            )

    def get_proposal(self, proposal_id: str) -> PortfolioProposal | None:
        """Retrieve prepared proposal by ID."""
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT p.id, p.base_revision, p.origin, p.thread_id, p.run_id,
                       p.before_artifact_id, p.after_artifact_id, p.differences_json,
                       p.warnings_json, p.status, p.created_at,
                       r.previous_revision, r.new_revision, r.committed_at as receipt_committed_at
                FROM proposals p
                LEFT JOIN proposal_receipts r ON p.id = r.proposal_id
                WHERE p.id = ?;
                """,
                (proposal_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_proposal(row)

    def get_proposal_detail(self, proposal_id: str) -> PortfolioProposalDetail | None:
        """Retrieve proposal with human-reviewable before/after content and differences."""
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT p.id, p.base_revision, p.origin, p.thread_id, p.run_id,
                       p.before_artifact_id, p.after_artifact_id, p.differences_json,
                       p.warnings_json, p.status, p.created_at,
                       r.previous_revision, r.new_revision, r.committed_at as receipt_committed_at
                FROM proposals p
                LEFT JOIN proposal_receipts r ON p.id = r.proposal_id
                WHERE p.id = ?;
                """,
                (proposal_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            base_proposal = self._row_to_proposal(row)
            before_payload = (
                self.get_artifact(row["before_artifact_id"], projection="human")
                if row["before_artifact_id"]
                else None
            )
            after_payload = self.get_artifact(row["after_artifact_id"], projection="human")

            before_portfolio = SavedPortfolio.model_validate(before_payload) if before_payload else None
            after_portfolio = SavedPortfolio.model_validate(after_payload) if after_payload else None

            return PortfolioProposalDetail(
                **base_proposal.model_dump(),
                before_portfolio=before_portfolio,
                after_portfolio=after_portfolio,
            )

    def list_proposals(self, status: ProposalStatus | None = None) -> list[PortfolioProposal]:
        """List proposals, optionally filtered by status, ordered by creation time descending."""
        with self.connect() as conn:
            if status is not None:
                cursor = conn.execute(
                    """
                    SELECT p.id, p.base_revision, p.origin, p.thread_id, p.run_id,
                           p.before_artifact_id, p.after_artifact_id, p.differences_json,
                           p.warnings_json, p.status, p.created_at,
                           r.previous_revision, r.new_revision, r.committed_at as receipt_committed_at
                    FROM proposals p
                    LEFT JOIN proposal_receipts r ON p.id = r.proposal_id
                    WHERE p.status = ?
                    ORDER BY p.created_at DESC;
                    """,
                    (status,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT p.id, p.base_revision, p.origin, p.thread_id, p.run_id,
                           p.before_artifact_id, p.after_artifact_id, p.differences_json,
                           p.warnings_json, p.status, p.created_at,
                           r.previous_revision, r.new_revision, r.committed_at as receipt_committed_at
                    FROM proposals p
                    LEFT JOIN proposal_receipts r ON p.id = r.proposal_id
                    ORDER BY p.created_at DESC;
                    """
                )
            rows = cursor.fetchall()
            return [self._row_to_proposal(row) for row in rows]

    def confirm_proposal(
        self,
        proposal_id: str,
    ) -> tuple[RevisionedPortfolio, ProposalReceipt]:
        """Atomically commit a proposal, creating a new revision, receipt, updating current pointer, and marking competing proposals stale."""
        with self.connect() as conn:
            # Reserve the writer before reading status or allocating the next revision.
            conn.execute("BEGIN IMMEDIATE;")
            cursor = conn.execute(
                """
                SELECT p.id, p.base_revision, p.status, p.after_artifact_id,
                       r.previous_revision, r.new_revision, r.committed_at as receipt_committed_at
                FROM proposals p
                LEFT JOIN proposal_receipts r ON p.id = r.proposal_id
                WHERE p.id = ?;
                """,
                (proposal_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise ProposalNotFoundError(f"Proposal '{proposal_id}' not found.")

            status = row["status"]
            if status == "applied":
                if row["new_revision"] is None:
                    raise RuntimeError(f"Applied proposal {proposal_id} is missing receipt.")
                rev_cursor = conn.execute(
                    "SELECT revision, committed_at, payload FROM portfolio_revisions WHERE revision = ?;",
                    (row["new_revision"],),
                )
                rev_row = rev_cursor.fetchone()
                if not rev_row:
                    raise RuntimeError(f"Missing revision {row['new_revision']} for applied proposal {proposal_id}")
                portfolio = SavedPortfolio.model_validate(json.loads(rev_row["payload"]))
                receipt = ProposalReceipt(
                    proposal_id=proposal_id,
                    previous_revision=row["previous_revision"],
                    new_revision=row["new_revision"],
                    committed_at=datetime.fromisoformat(row["receipt_committed_at"]),
                )
                return RevisionedPortfolio(
                    revision=rev_row["revision"],
                    portfolio=portfolio,
                    committed_at=datetime.fromisoformat(rev_row["committed_at"]),
                ), receipt

            if status == "cancelled":
                raise InvalidProposalStateError(f"Proposal '{proposal_id}' has been cancelled and cannot be confirmed.")

            if status == "stale":
                raise InvalidProposalStateError(f"Proposal '{proposal_id}' is stale and cannot be confirmed.")

            if status != "pending":
                raise InvalidProposalStateError(f"Proposal '{proposal_id}' cannot be confirmed in status '{status}'.")

            # Recheck current revision inside transaction
            curr_cursor = conn.execute("SELECT current_revision FROM portfolio_current WHERE id = 1;")
            curr_row = curr_cursor.fetchone()
            current_rev = curr_row["current_revision"] if curr_row else None

            if current_rev != row["base_revision"]:
                raise StaleBaseRevisionError(
                    f"Proposal base revision {row['base_revision']} is stale; current revision is {current_rev}."
                )

            # Retrieve exact stored artifact payload
            art_cursor = conn.execute("SELECT payload_json FROM artifacts WHERE id = ?;", (row["after_artifact_id"],))
            art_row = art_cursor.fetchone()
            if not art_row:
                raise RuntimeError(f"Missing after artifact {row['after_artifact_id']} for proposal {proposal_id}")
            stored_payload_json = art_row["payload_json"]
            candidate_portfolio = SavedPortfolio.model_validate(json.loads(stored_payload_json))

            # Determine next revision number
            max_cursor = conn.execute("SELECT MAX(revision) FROM portfolio_revisions;")
            max_row = max_cursor.fetchone()
            next_rev = 1 if (not max_row or max_row[0] is None) else (max_row[0] + 1)

            now = datetime.now(UTC)
            now_iso = now.isoformat()

            conn.execute(
                "INSERT INTO portfolio_revisions (revision, committed_at, payload) VALUES (?, ?, ?);",
                (next_rev, now_iso, stored_payload_json),
            )

            conn.execute(
                """
                INSERT INTO portfolio_current (id, current_revision, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET current_revision = excluded.current_revision, updated_at = excluded.updated_at;
                """,
                (next_rev, now_iso),
            )

            conn.execute(
                "UPDATE proposals SET status = 'applied' WHERE id = ?;",
                (proposal_id,),
            )

            conn.execute(
                """
                INSERT INTO proposal_receipts (proposal_id, previous_revision, new_revision, committed_at)
                VALUES (?, ?, ?, ?);
                """,
                (proposal_id, row["base_revision"], next_rev, now_iso),
            )

            # Mark competing pending proposals stale
            conn.execute(
                "UPDATE proposals SET status = 'stale' WHERE status = 'pending' AND id != ?;",
                (proposal_id,),
            )

            revisioned = RevisionedPortfolio(
                revision=next_rev,
                portfolio=candidate_portfolio,
                committed_at=now,
            )
            receipt = ProposalReceipt(
                proposal_id=proposal_id,
                previous_revision=row["base_revision"],
                new_revision=next_rev,
                committed_at=now,
            )
            return revisioned, receipt


    def cancel_proposal(self, proposal_id: str) -> ProposalStatus:
        """Atomically cancel a proposal. Harmless if already cancelled; conflict if applied."""
        with self.connect() as conn:
            cursor = conn.execute(
                "SELECT id, status FROM proposals WHERE id = ?;",
                (proposal_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise ProposalNotFoundError(f"Proposal '{proposal_id}' not found.")

            status = row["status"]
            if status == "applied":
                raise InvalidProposalStateError(f"Proposal '{proposal_id}' has already been applied and cannot be cancelled.")
            if status == "cancelled":
                return "cancelled"

            conn.execute(
                "UPDATE proposals SET status = 'cancelled' WHERE id = ?;",
                (proposal_id,),
            )
            return "cancelled"

    # =========================================================================
    # Conversation & Run Lifecycle Methods (Ticket 03)
    # =========================================================================

    def create_thread(self, thread_id: str | None = None) -> ThreadSummary:
        """Create a new conversation thread."""
        tid = thread_id or f"th-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO threads (id, created_at, updated_at) VALUES (?, ?, ?);",
                (tid, now_iso, now_iso),
            )
        return ThreadSummary(
            id=tid,
            created_at=now,
            updated_at=now,
            last_message_preview=None,
        )

    def list_threads(self) -> list[ThreadSummary]:
        """List conversation threads ordered by updated_at descending with last message preview."""
        with self.connect() as conn:
            cursor = conn.execute("""
                SELECT t.id, t.created_at, t.updated_at,
                       (SELECT m.text FROM messages m WHERE m.thread_id = t.id ORDER BY m.created_at DESC LIMIT 1) as last_msg,
                       (SELECT m.text FROM messages m WHERE m.thread_id = t.id AND m.role = 'user'
                        ORDER BY m.created_at ASC LIMIT 1) as title
                FROM threads t
                ORDER BY t.updated_at DESC;
            """)
            rows = cursor.fetchall()
            summaries: list[ThreadSummary] = []
            for r in rows:
                preview = r["last_msg"]
                if preview and len(preview) > 120:
                    preview = preview[:117] + "..."
                title = r["title"]
                if title and len(title) > 120:
                    title = title[:117] + "..."
                summaries.append(
                    ThreadSummary(
                        id=r["id"],
                        created_at=datetime.fromisoformat(r["created_at"]),
                        updated_at=datetime.fromisoformat(r["updated_at"]),
                        last_message_preview=preview,
                        title=title,
                    )
                )
            return summaries

    def get_thread(self, thread_id: str) -> ThreadDetail | None:
        """Get thread detail including ordered messages and runs."""
        with self.connect() as conn:
            cursor = conn.execute("SELECT id, created_at, updated_at FROM threads WHERE id = ?;", (thread_id,))
            t_row = cursor.fetchone()
            if not t_row:
                return None

            msg_cursor = conn.execute(
                """
                SELECT id, thread_id, run_id, role, text, citations_json, artifact_ids_json,
                       clarification_required_json, proposal_ids_json, tool_outcomes_json, created_at
                FROM messages
                WHERE thread_id = ?
                ORDER BY created_at ASC;
                """,
                (thread_id,),
            )
            messages = [self._row_to_message(m) for m in msg_cursor.fetchall()]

            run_cursor = conn.execute(
                """
                SELECT id, thread_id, client_request_id, status, bound_revision,
                       created_at, updated_at, error, message_ids_json, artifact_ids_json
                FROM runs
                WHERE thread_id = ?
                ORDER BY created_at ASC;
                """,
                (thread_id,),
            )
            runs = [self._row_to_run(rn) for rn in run_cursor.fetchall()]

            return ThreadDetail(
                id=t_row["id"],
                created_at=datetime.fromisoformat(t_row["created_at"]),
                updated_at=datetime.fromisoformat(t_row["updated_at"]),
                messages=messages,
                runs=runs,
            )

    def read_messages(
        self, thread_id: str, *, limit: int = 20,
        before_id: str | None = None, through_id: str,
    ) -> list[MessageRecord]:
        """Read a bounded chronological slice, capped at the accepted user message."""
        if not 1 <= limit <= 20:
            raise ValueError("Message limit must be between 1 and 20.")
        with self.connect() as conn:
            end = conn.execute(
                "SELECT rowid FROM messages WHERE id = ? AND thread_id = ?;",
                (through_id, thread_id),
            ).fetchone()
            if end is None:
                raise ValueError("Message boundary not found.")
            cutoff = end[0] + 1
            if before_id is not None:
                before = conn.execute(
                    "SELECT rowid FROM messages WHERE id = ? AND thread_id = ?;",
                    (before_id, thread_id),
                ).fetchone()
                if before is None or before[0] > end[0]:
                    raise ValueError("Message cursor not found.")
                cutoff = before[0]
            rows = conn.execute(
                "SELECT * FROM messages WHERE thread_id = ? AND rowid < ? "
                "ORDER BY rowid DESC LIMIT ?;", (thread_id, cutoff, limit),
            ).fetchall()
            return [self._row_to_message(row) for row in reversed(rows)]

    def submit_message(
        self,
        thread_id: str,
        request: SubmitMessageRequest,
        bound_revision: int | None,
    ) -> tuple[RunRecord, bool]:
        """
        Submit a message to a thread transactionally.
        Returns (RunRecord, is_new).
        Raises ThreadNotFoundError if thread does not exist.
        Raises RunConflictError if payload differs for matching client_request_id or competing run is running.
        """
        # Canonical hash of request payload
        payload_dict = {
            "client_request_id": request.client_request_id,
            "text": request.text,
            "workflow_input": request.workflow_input,
            "hypothetical_artifact_id": request.hypothetical_artifact_id,
        }
        canonical_str = json.dumps(payload_dict, sort_keys=True, cls=DecimalEncoder)
        payload_hash = hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

        with self.connect() as conn:
            # Check thread existence
            cursor = conn.execute("SELECT id FROM threads WHERE id = ?;", (thread_id,))
            if not cursor.fetchone():
                raise ThreadNotFoundError(f"Thread '{thread_id}' not found.")

            # Check if run with same client_request_id already exists in this thread
            cursor = conn.execute(
                """
                SELECT id, thread_id, client_request_id, request_payload_hash, status,
                       bound_revision, created_at, updated_at, error, message_ids_json, artifact_ids_json
                FROM runs
                WHERE thread_id = ? AND client_request_id = ?;
                """,
                (thread_id, request.client_request_id),
            )
            existing_run = cursor.fetchone()
            if existing_run:
                if existing_run["request_payload_hash"] == payload_hash:
                    # Idempotent match: return existing run
                    return self._row_to_run(existing_run), False
                else:
                    raise RunConflictError(
                        f"Client request ID '{request.client_request_id}' has already been submitted with a different payload."
                    )

            # Check if there is already an active running run on this thread
            cursor = conn.execute(
                "SELECT id FROM runs WHERE thread_id = ? AND status = 'running';",
                (thread_id,),
            )
            if cursor.fetchone():
                raise RunConflictError(
                    f"Thread '{thread_id}' already has an active running run. Only one running run per thread is permitted."
                )

            # Insert new run first, then initial user message (to satisfy foreign key from messages to runs)
            run_id = f"run-{uuid.uuid4().hex[:12]}"
            msg_id = f"msg-{uuid.uuid4().hex[:12]}"
            now = datetime.now(UTC)
            now_iso = now.isoformat()

            conn.execute(
                """
                INSERT INTO runs (
                    id, thread_id, client_request_id, request_payload_hash, status,
                    bound_revision, created_at, updated_at, error, message_ids_json, artifact_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    run_id,
                    thread_id,
                    request.client_request_id,
                    payload_hash,
                    "running",
                    bound_revision,
                    now_iso,
                    now_iso,
                    None,
                    json.dumps([msg_id]),
                    "[]",
                ),
            )

            conn.execute(
                """
                INSERT INTO messages (
                    id, thread_id, run_id, role, text, citations_json, artifact_ids_json,
                    clarification_required_json, proposal_ids_json, tool_outcomes_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    msg_id,
                    thread_id,
                    run_id,
                    "user",
                    request.text,
                    "[]",
                    "[]",
                    None,
                    "[]",
                    "[]",
                    now_iso,
                ),
            )

            # Update thread updated_at
            conn.execute("UPDATE threads SET updated_at = ? WHERE id = ?;", (now_iso, thread_id))

            run_record = RunRecord(
                id=run_id,
                thread_id=thread_id,
                client_request_id=request.client_request_id,
                status="running",
                bound_revision=bound_revision,
                created_at=now,
                updated_at=now,
                error=None,
                message_ids=[msg_id],
                artifact_ids=[],
            )
            return run_record, True

    def get_run(self, thread_id: str, run_id: str) -> RunRecord | None:
        """Get run record ensuring cross-thread lookup returns None."""
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT id, thread_id, client_request_id, status, bound_revision,
                       created_at, updated_at, error, message_ids_json, artifact_ids_json
                FROM runs
                WHERE id = ? AND thread_id = ?;
                """,
                (run_id, thread_id),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_run(row)

    def cancel_run(self, thread_id: str, run_id: str) -> RunRecord | None:
        """
        Cancel a run if currently running.
        Atomically updates status to 'cancelled'. If already in a terminal state, preserves it.
        Returns the updated/current RunRecord, or None if not found in this thread.
        """
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT id, thread_id, client_request_id, status, bound_revision,
                       created_at, updated_at, error, message_ids_json, artifact_ids_json
                FROM runs
                WHERE id = ? AND thread_id = ?;
                """,
                (run_id, thread_id),
            )
            row = cursor.fetchone()
            if not row:
                return None

            status = row["status"]
            if status == "running":
                now_iso = datetime.now(UTC).isoformat()
                conn.execute(
                    "UPDATE runs SET status = 'cancelled', updated_at = ? WHERE id = ? AND status = 'running';",
                    (now_iso, run_id),
                )
                conn.execute("UPDATE threads SET updated_at = ? WHERE id = ?;", (now_iso, thread_id))
                # Re-fetch
                cursor = conn.execute(
                    """
                    SELECT id, thread_id, client_request_id, status, bound_revision,
                           created_at, updated_at, error, message_ids_json, artifact_ids_json
                    FROM runs
                    WHERE id = ?;
                    """,
                    (run_id,),
                )
                row = cursor.fetchone()

            return self._row_to_run(row)

    def update_run(
        self,
        run_id: str,
        status: RunStatus,
        error: str | None = None,
        additional_message_ids: list[str] | None = None,
        additional_artifact_ids: list[str] | None = None,
    ) -> RunRecord | None:
        """
        Atomically update run status, ensuring terminal states cannot be overwritten.
        Only transitions if current status is 'running'.
        """
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT id, thread_id, client_request_id, status, bound_revision,
                       created_at, updated_at, error, message_ids_json, artifact_ids_json
                FROM runs
                WHERE id = ?;
                """,
                (run_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            current_status = row["status"]
            if current_status != "running":
                # Terminal state already reached; do not overwrite!
                return self._row_to_run(row)

            msg_ids = json.loads(row["message_ids_json"])
            if additional_message_ids:
                for mid in additional_message_ids:
                    if mid not in msg_ids:
                        msg_ids.append(mid)

            art_ids = json.loads(row["artifact_ids_json"])
            if additional_artifact_ids:
                for aid in additional_artifact_ids:
                    if aid not in art_ids:
                        art_ids.append(aid)

            conn.execute(
                """
                UPDATE runs
                SET status = ?, updated_at = ?, error = ?, message_ids_json = ?, artifact_ids_json = ?
                WHERE id = ? AND status = 'running';
                """,
                (
                    status,
                    now_iso,
                    error,
                    json.dumps(msg_ids),
                    json.dumps(art_ids),
                    run_id,
                ),
            )
            conn.execute("UPDATE threads SET updated_at = ? WHERE id = ?;", (now_iso, row["thread_id"]))

            # Re-read
            cursor = conn.execute(
                """
                SELECT id, thread_id, client_request_id, status, bound_revision,
                       created_at, updated_at, error, message_ids_json, artifact_ids_json
                FROM runs
                WHERE id = ?;
                """,
                (run_id,),
            )
            updated_row = cursor.fetchone()
            return self._row_to_run(updated_row)

    def add_message(
        self,
        thread_id: str,
        role: MessageRole,
        text: str,
        run_id: str | None = None,
        citations: list[dict[str, Any]] | None = None,
        artifact_ids: list[str] | None = None,
        clarification_required: dict[str, Any] | None = None,
        proposal_ids: list[str] | None = None,
        tool_outcomes: list[ToolOutcome] | None = None,
    ) -> MessageRecord:
        """Add an assistant or user message to a thread."""
        msg_id = f"msg-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        tool_outcomes_dicts = [t.model_dump(mode="python") for t in (tool_outcomes or [])]

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO messages (
                    id, thread_id, run_id, role, text, citations_json, artifact_ids_json,
                    clarification_required_json, proposal_ids_json, tool_outcomes_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    msg_id,
                    thread_id,
                    run_id,
                    role,
                    text,
                    json.dumps(citations or []),
                    json.dumps(artifact_ids or []),
                    json.dumps(clarification_required) if clarification_required else None,
                    json.dumps(proposal_ids or []),
                    json.dumps(tool_outcomes_dicts, cls=DecimalEncoder),
                    now_iso,
                ),
            )
            conn.execute("UPDATE threads SET updated_at = ? WHERE id = ?;", (now_iso, thread_id))

        return MessageRecord(
            id=msg_id,
            thread_id=thread_id,
            run_id=run_id,
            role=role,
            text=text,
            citations=citations or [],
            artifact_ids=artifact_ids or [],
            clarification_required=clarification_required,
            proposal_ids=proposal_ids or [],
            tool_outcomes=tool_outcomes or [],
            created_at=now,
        )

    def store_private_continuation(self, run_id: str, continuation: Any) -> None:
        """Store private model continuation items in server-only table."""
        now_iso = datetime.now(UTC).isoformat()
        continuation_json = json.dumps(continuation, cls=DecimalEncoder)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO private_continuation (run_id, continuation_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET continuation_json = excluded.continuation_json, updated_at = excluded.updated_at;
                """,
                (run_id, continuation_json, now_iso),
            )

    def get_private_continuation(self, run_id: str) -> Any | None:
        """Retrieve private continuation for a run."""
        with self.connect() as conn:
            cursor = conn.execute("SELECT continuation_json FROM private_continuation WHERE run_id = ?;", (run_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return json.loads(row["continuation_json"])

    def recover_interrupted_runs(self) -> int:
        """
        Mark all currently 'running' runs as 'interrupted' upon process restart without replaying.
        Returns the number of runs transitioned.
        """
        now_iso = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE runs SET status = 'interrupted', updated_at = ? WHERE status = 'running';",
                (now_iso,),
            )
            return cursor.rowcount

    def _row_to_run(self, row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            id=row["id"],
            thread_id=row["thread_id"],
            client_request_id=row["client_request_id"],
            status=row["status"],
            bound_revision=row["bound_revision"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            error=row["error"],
            message_ids=json.loads(row["message_ids_json"]),
            artifact_ids=json.loads(row["artifact_ids_json"]),
        )

    def _row_to_message(self, row: sqlite3.Row) -> MessageRecord:
        tool_outcomes_raw = json.loads(row["tool_outcomes_json"])
        outcomes = [ToolOutcome.model_validate(o) for o in tool_outcomes_raw]
        clarification = json.loads(row["clarification_required_json"]) if row["clarification_required_json"] else None
        return MessageRecord(
            id=row["id"],
            thread_id=row["thread_id"],
            run_id=row["run_id"],
            role=row["role"],
            text=row["text"],
            citations=json.loads(row["citations_json"]),
            artifact_ids=json.loads(row["artifact_ids_json"]),
            clarification_required=clarification,
            proposal_ids=json.loads(row["proposal_ids_json"]),
            tool_outcomes=outcomes,
            created_at=datetime.fromisoformat(row["created_at"]),
        )



