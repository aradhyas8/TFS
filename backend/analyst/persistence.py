"""SQLite persistence foundation for portfolio revisions, artifacts, and proposals."""

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
    ArtifactReference,
    DifferenceItem,
    PortfolioProposal,
    PortfolioProposalDetail,
    ProposalOrigin,
    ProposalReceipt,
    ProposalStatus,
    RevisionedPortfolio,
    SavedPortfolio,
    build_artifact_payload,
)

SCHEMA_VERSION = 1


class SchemaMigrationError(RuntimeError):
    """Raised when database schema version is incompatible."""


class LegacyMigrationError(RuntimeError):
    """Raised when legacy JSON portfolio cannot be parsed or validated."""


class StaleBaseRevisionError(RuntimeError):
    """Raised when base revision does not match current committed revision."""




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
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_schema_version(self) -> int:
        if not self.path.exists():
            return 0
        with self.connect() as conn:
            cursor = conn.execute("PRAGMA user_version;")
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def initialize(self) -> None:
        """Initialize database schema idempotently."""
        with self.connect() as conn:
            cursor = conn.execute("PRAGMA user_version;")
            current_version = int(cursor.fetchone()[0])
            if current_version == 0:
                self._apply_initial_schema(conn)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
            elif current_version > SCHEMA_VERSION:
                raise SchemaMigrationError(
                    f"Database schema version {current_version} is higher than supported {SCHEMA_VERSION}."
                )

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
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?);",
            (SCHEMA_VERSION, datetime.now(UTC).isoformat()),
        )

    def store_artifact(
        self,
        reference: ArtifactReference,
        portfolio: SavedPortfolio,
    ) -> None:
        """Store immutable artifact payload and metadata."""
        # Store human-projection payload natively with Decimal serialization
        raw_payload = build_artifact_payload(portfolio, projection="human")
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

    def get_artifact(
        self,
        artifact_id: str,
        projection: Literal["human", "model"] = "human",
    ) -> dict[str, Any] | None:
        """Retrieve stored artifact payload with the chosen projection."""
        with self.connect() as conn:
            cursor = conn.execute(
                "SELECT payload_json FROM artifacts WHERE id = ?;",
                (artifact_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            data = json.loads(row["payload_json"])

            if projection == "model":
                data["average_costs"] = {}
                for item in data.get("unresolved", []):
                    item["average_cost"] = None
            return data

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


