from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


QUEUE_STATUSES = (
    "pending",
    "opening",
    "filling",
    "waiting_user",
    "ready_to_submit",
    "submitted",
    "failed",
    "skipped",
)

# A transition is intentionally explicit.  In particular, a worker cannot jump
# from pending directly to submitted and a failed item must be deliberately
# retried before it can run again.
QUEUE_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"opening", "failed", "skipped"}),
    "opening": frozenset(
        {"filling", "waiting_user", "ready_to_submit", "failed", "skipped"}
    ),
    "filling": frozenset(
        {"waiting_user", "ready_to_submit", "failed", "skipped"}
    ),
    "waiting_user": frozenset(
        {"opening", "filling", "ready_to_submit", "failed", "skipped"}
    ),
    "ready_to_submit": frozenset(
        {"filling", "waiting_user", "submitted", "failed", "skipped"}
    ),
    "failed": frozenset({"pending", "skipped"}),
    "submitted": frozenset(),
    "skipped": frozenset(),
}


class DatabaseError(RuntimeError):
    """Base class for persistence errors that are safe to show in the UI."""


class RecordNotFound(DatabaseError):
    """Raised when the requested job, application, or queue item is missing."""


class DuplicateApplicationError(DatabaseError):
    """Raised when code attempts to apply to a job already submitted."""


class InvalidStateTransition(DatabaseError):
    """Raised when an application queue transition violates the state machine."""


class ReviewRequired(DatabaseError):
    """Raised when a queue item is not safe to mark ready for submission."""


class SubmissionConfirmationRequired(DatabaseError):
    """Raised when final submission has not been explicitly confirmed."""


class RetryLimitExceeded(DatabaseError):
    """Raised when a failed queue item has used all of its retry allowance."""


def utc_now() -> str:
    """Return a stable, sortable UTC timestamp for SQLite text columns."""

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonicalize_job_url(value: str) -> str:
    """Normalize a job URL for duplicate detection without changing its meaning.

    Fragments and common marketing parameters are ignored.  Application-critical
    query parameters (for example ``folderId``) are retained.
    """

    raw = str(value or "").strip()
    if not raw:
        raise ValueError("job_url cannot be empty")
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise ValueError("job_url is not a valid URL") from exc
    scheme = parts.scheme.casefold()
    if scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("job_url must be an http or https URL")

    hostname = parts.hostname.casefold()
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    if parts.username or parts.password:
        # Career links should never contain credentials.  Rejecting them also
        # prevents accidental persistence of secrets copied from a browser.
        raise ValueError("job_url must not contain credentials")

    ignored = {"gclid", "fbclid", "mc_cid", "mc_eid", "ref", "referrer"}
    query_items = []
    for key, item_value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.casefold()
        if lowered.startswith("utm_") or lowered in ignored:
            continue
        query_items.append((key, item_value))
    query_items.sort(key=lambda item: (item[0].casefold(), item[1]))

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, urlencode(query_items, doseq=True), ""))


def _to_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(mode="json", by_alias=False))
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    raise TypeError("expected a mapping, dataclass, or Pydantic model")


def _pick(data: Mapping[str, Any], *names: str, default: Any = "") -> Any:
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return default


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple, set)):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, (dict, Mapping)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value).strip()


def _json(value: Any, *, fallback: Any) -> str:
    if value is None:
        value = fallback
    if not isinstance(value, (Mapping, list, tuple, str, int, float, bool)):
        try:
            value = _to_mapping(value)
        except TypeError:
            value = str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _load_json(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _score(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError("match_score must be a number from 0 to 100") from exc
    if not 0 <= score <= 100:
        raise ValueError("match_score must be from 0 to 100")
    return score


class JobAgentDatabase:
    """Small transactional SQLite repository for jobs and application queues.

    A new connection is used for every operation, which makes this safe for
    Streamlit reruns and browser worker threads.  ``BEGIN IMMEDIATE`` serializes
    identity checks with inserts, so duplicate prevention does not depend solely
    on a prior read.
    """

    SCHEMA_VERSION = 1

    def __init__(self, database_path: str | Path = "data/app.db") -> None:
        raw_path = str(database_path)
        self._anchor: sqlite3.Connection | None = None
        self._uri = raw_path == ":memory:"
        if self._uri:
            self.database_path: Path | str = ":memory:"
            self._target = f"file:ai_job_agent_{id(self)}?mode=memory&cache=shared"
            self._anchor = self._connect()
        else:
            path = Path(database_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.database_path = path
            self._target = str(path)
        self.initialize()

    def close(self) -> None:
        if self._anchor is not None:
            self._anchor.close()
            self._anchor = None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._target,
            timeout=10.0,
            uri=self._uri,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        # WAL is unavailable for shared in-memory databases but harmless there.
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        with self.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT NOT NULL DEFAULT '',
                    job_type TEXT NOT NULL DEFAULT '',
                    department TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    requirements TEXT NOT NULL DEFAULT '',
                    job_url TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'generic',
                    publish_date TEXT NOT NULL DEFAULT '',
                    match_score INTEGER CHECK(match_score IS NULL OR match_score BETWEEN 0 AND 100),
                    match_level TEXT NOT NULL DEFAULT '',
                    match_json TEXT NOT NULL DEFAULT '{{}}',
                    raw_json TEXT NOT NULL DEFAULT '{{}}',
                    discovered_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_pk INTEGER NOT NULL,
                    job_id TEXT NOT NULL DEFAULT '',
                    company TEXT NOT NULL,
                    job_title TEXT NOT NULL,
                    job_url TEXT NOT NULL,
                    match_score INTEGER CHECK(match_score IS NULL OR match_score BETWEEN 0 AND 100),
                    resume_version TEXT NOT NULL DEFAULT '',
                    application_date TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN {QUEUE_STATUSES}),
                    notes TEXT NOT NULL DEFAULT '',
                    review_json TEXT NOT NULL DEFAULT '{{}}',
                    needs_user_confirmation TEXT NOT NULL DEFAULT '[]',
                    required_field_missing INTEGER NOT NULL DEFAULT 1,
                    is_demo INTEGER NOT NULL DEFAULT 0,
                    submitted_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(job_pk) REFERENCES jobs(id) ON DELETE RESTRICT,
                    UNIQUE(job_pk)
                )
                """
            )
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS application_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    application_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN {QUEUE_STATUSES}),
                    review_json TEXT NOT NULL DEFAULT '{{}}',
                    needs_user_confirmation TEXT NOT NULL DEFAULT '[]',
                    required_field_missing INTEGER NOT NULL DEFAULT 1,
                    retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
                    max_retries INTEGER NOT NULL DEFAULT 3 CHECK(max_retries BETWEEN 0 AND 20),
                    last_error TEXT NOT NULL DEFAULT '',
                    error_screenshot TEXT NOT NULL DEFAULT '',
                    reviewed_at TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE CASCADE,
                    UNIQUE(application_id)
                )
                """
            )
            self._migrate_columns(connection)
            self._create_indexes(connection)
            connection.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")

    @staticmethod
    def _migrate_columns(connection: sqlite3.Connection) -> None:
        """Add backward-compatible columns without deleting or rewriting rows."""

        additions: dict[str, dict[str, str]] = {
            "jobs": {
                "job_id": "TEXT NOT NULL DEFAULT ''",
                "location": "TEXT NOT NULL DEFAULT ''",
                "job_type": "TEXT NOT NULL DEFAULT ''",
                "department": "TEXT NOT NULL DEFAULT ''",
                "description": "TEXT NOT NULL DEFAULT ''",
                "requirements": "TEXT NOT NULL DEFAULT ''",
                "canonical_url": "TEXT NOT NULL DEFAULT ''",
                "source": "TEXT NOT NULL DEFAULT 'generic'",
                "publish_date": "TEXT NOT NULL DEFAULT ''",
                "match_score": "INTEGER",
                "match_level": "TEXT NOT NULL DEFAULT ''",
                "match_json": "TEXT NOT NULL DEFAULT '{}'",
                "raw_json": "TEXT NOT NULL DEFAULT '{}'",
                "discovered_at": "TEXT NOT NULL DEFAULT ''",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
            },
            "applications": {
                "job_id": "TEXT NOT NULL DEFAULT ''",
                "match_score": "INTEGER",
                "resume_version": "TEXT NOT NULL DEFAULT ''",
                "application_date": "TEXT NOT NULL DEFAULT ''",
                "status": "TEXT NOT NULL DEFAULT 'pending'",
                "notes": "TEXT NOT NULL DEFAULT ''",
                "review_json": "TEXT NOT NULL DEFAULT '{}'",
                "needs_user_confirmation": "TEXT NOT NULL DEFAULT '[]'",
                "required_field_missing": "INTEGER NOT NULL DEFAULT 1",
                "is_demo": "INTEGER NOT NULL DEFAULT 0",
                "submitted_at": "TEXT",
                "created_at": "TEXT NOT NULL DEFAULT ''",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
            },
            "application_queue": {
                "status": "TEXT NOT NULL DEFAULT 'pending'",
                "review_json": "TEXT NOT NULL DEFAULT '{}'",
                "needs_user_confirmation": "TEXT NOT NULL DEFAULT '[]'",
                "required_field_missing": "INTEGER NOT NULL DEFAULT 1",
                "retry_count": "INTEGER NOT NULL DEFAULT 0",
                "max_retries": "INTEGER NOT NULL DEFAULT 3",
                "last_error": "TEXT NOT NULL DEFAULT ''",
                "error_screenshot": "TEXT NOT NULL DEFAULT ''",
                "reviewed_at": "TEXT",
                "started_at": "TEXT",
                "completed_at": "TEXT",
                "created_at": "TEXT NOT NULL DEFAULT ''",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
            },
        }
        for table, definitions in additions.items():
            existing = {
                str(row["name"])
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, definition in definitions.items():
                if name not in existing:
                    # Names and definitions are static constants above, never user input.
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

        # Backfill only values that older versions did not have.  Existing data is
        # otherwise left untouched.
        now = utc_now()
        connection.execute(
            "UPDATE jobs SET discovered_at=? WHERE discovered_at=''", (now,)
        )
        connection.execute("UPDATE jobs SET updated_at=? WHERE updated_at=''", (now,))
        connection.execute(
            "UPDATE applications SET application_date=? WHERE application_date=''", (now,)
        )
        connection.execute(
            "UPDATE applications SET created_at=? WHERE created_at=''", (now,)
        )
        connection.execute(
            "UPDATE applications SET updated_at=? WHERE updated_at=''", (now,)
        )
        connection.execute(
            "UPDATE application_queue SET created_at=? WHERE created_at=''", (now,)
        )
        connection.execute(
            "UPDATE application_queue SET updated_at=? WHERE updated_at=''", (now,)
        )

        rows = connection.execute(
            "SELECT id, job_url FROM jobs WHERE canonical_url=''"
        ).fetchall()
        for row in rows:
            try:
                canonical = canonicalize_job_url(str(row["job_url"]))
            except ValueError:
                canonical = f"legacy://job/{row['id']}"
            connection.execute(
                "UPDATE jobs SET canonical_url=? WHERE id=?", (canonical, row["id"])
            )

    @staticmethod
    def _create_indexes(connection: sqlite3.Connection) -> None:
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_jobs_match_score "
            "ON jobs(match_score DESC)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_company_location "
            "ON jobs(company, location)",
            "CREATE INDEX IF NOT EXISTS idx_applications_status "
            "ON applications(status, updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_queue_status "
            "ON application_queue(status, updated_at)",
        ):
            connection.execute(statement)
        # A legacy database can already contain duplicates.  Initialization must
        # never delete those rows; transactional repository methods still prevent
        # new duplicates.  Clean databases receive database-level constraints too.
        for statement in (
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_canonical_url ON jobs(canonical_url)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_source_job_id "
            "ON jobs(source, job_id) WHERE job_id <> ''",
        ):
            try:
                connection.execute(statement)
            except sqlite3.IntegrityError:
                pass

    @staticmethod
    def _job_payload(job: Any, assessment: Any | None = None) -> dict[str, Any]:
        data = _to_mapping(job)
        assessment_data = _to_mapping(assessment) if assessment is not None else {}
        job_url = _text(_pick(data, "job_url", "url"))
        title = _text(_pick(data, "title", "job_title"))
        company = _text(_pick(data, "company"))
        if not title:
            raise ValueError("job title cannot be empty")
        if not company:
            raise ValueError("job company cannot be empty")
        score = _score(_pick(assessment_data, "match_score", "score", default=None))
        if score is None:
            score = _score(_pick(data, "match_score", "score", default=None))
        return {
            "job_id": _text(_pick(data, "job_id", "external_job_id", "id")),
            "title": title,
            "company": company,
            "location": _text(_pick(data, "location")),
            "job_type": _text(_pick(data, "job_type", "employment_type")),
            "department": _text(_pick(data, "department")),
            "description": _text(_pick(data, "description", "job_description")),
            "requirements": _text(_pick(data, "requirements")),
            "job_url": job_url,
            "canonical_url": canonicalize_job_url(job_url),
            "source": _text(_pick(data, "source", default="generic")) or "generic",
            "publish_date": _text(_pick(data, "publish_date", "published_at")),
            "match_score": score,
            "match_level": _text(
                _pick(assessment_data, "match_level", default=_pick(data, "match_level"))
            ),
            "match_json": _json(assessment_data, fallback={}),
            "raw_json": _json(data, fallback={}),
        }

    def upsert_job(self, job: Any, assessment: Any | None = None) -> dict[str, Any]:
        payload = self._job_payload(job, assessment)
        now = utc_now()
        with self.transaction() as connection:
            by_url = connection.execute(
                "SELECT id FROM jobs WHERE canonical_url=? ORDER BY id LIMIT 1",
                (payload["canonical_url"],),
            ).fetchone()
            by_external = None
            if payload["job_id"]:
                by_external = connection.execute(
                    "SELECT id FROM jobs WHERE source=? AND job_id=? ORDER BY id LIMIT 1",
                    (payload["source"], payload["job_id"]),
                ).fetchone()
            if by_url and by_external and by_url["id"] != by_external["id"]:
                raise DatabaseError(
                    "job URL and external job ID refer to different existing records"
                )
            existing = by_url or by_external
            values = (
                payload["job_id"],
                payload["title"],
                payload["company"],
                payload["location"],
                payload["job_type"],
                payload["department"],
                payload["description"],
                payload["requirements"],
                payload["job_url"],
                payload["canonical_url"],
                payload["source"],
                payload["publish_date"],
                payload["match_score"],
                payload["match_level"],
                payload["match_json"],
                payload["raw_json"],
                now,
            )
            if existing:
                job_pk = int(existing["id"])
                connection.execute(
                    """
                    UPDATE jobs SET
                        job_id=?, title=?, company=?, location=?, job_type=?,
                        department=?, description=?, requirements=?, job_url=?,
                        canonical_url=?, source=?, publish_date=?, match_score=?,
                        match_level=?, match_json=?, raw_json=?, updated_at=?
                    WHERE id=?
                    """,
                    (*values, job_pk),
                )
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO jobs (
                        job_id, title, company, location, job_type, department,
                        description, requirements, job_url, canonical_url, source,
                        publish_date, match_score, match_level, match_json, raw_json,
                        discovered_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*values[:-1], now, now),
                )
                job_pk = int(cursor.lastrowid)
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_pk,)).fetchone()
        return self._decode_job(row)

    save_job = upsert_job

    def get_job(self, job_pk: int) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_pk,)).fetchone()
        return self._decode_job(row) if row else None

    def find_job(
        self,
        *,
        job_url: str | None = None,
        source: str | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any] | None:
        if job_url:
            canonical = canonicalize_job_url(job_url)
            query, params = (
                "SELECT * FROM jobs WHERE canonical_url=? ORDER BY id LIMIT 1",
                (canonical,),
            )
        elif source is not None and job_id is not None:
            query, params = (
                "SELECT * FROM jobs WHERE source=? AND job_id=? ORDER BY id LIMIT 1",
                (str(source).strip() or "generic", str(job_id).strip()),
            )
        else:
            raise ValueError("provide job_url or both source and job_id")
        with closing(self._connect()) as connection:
            row = connection.execute(query, params).fetchone()
        return self._decode_job(row) if row else None

    def list_jobs(
        self,
        *,
        minimum_score: int | None = None,
        company: str | None = None,
        location: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not 1 <= int(limit) <= 1000:
            raise ValueError("limit must be from 1 to 1000")
        clauses: list[str] = []
        params: list[Any] = []
        if minimum_score is not None:
            clauses.append("match_score >= ?")
            params.append(_score(minimum_score))
        if company:
            clauses.append("company = ?")
            params.append(str(company).strip())
        if location:
            clauses.append("location = ?")
            params.append(str(location).strip())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM jobs{where} "
                "ORDER BY match_score IS NULL, match_score DESC, updated_at DESC LIMIT ?",
                (*params, int(limit)),
            ).fetchall()
        return [self._decode_job(row) for row in rows]

    def get_or_create_application(
        self,
        job_pk: int,
        *,
        match_score: int | None = None,
        resume_version: str = "",
        notes: str = "",
        is_demo: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        now = utc_now()
        with self.transaction() as connection:
            job = connection.execute("SELECT * FROM jobs WHERE id=?", (job_pk,)).fetchone()
            if not job:
                raise RecordNotFound(f"job {job_pk} does not exist")
            existing = connection.execute(
                "SELECT * FROM applications WHERE job_pk=?", (job_pk,)
            ).fetchone()
            if existing:
                return self._decode_application(existing), False
            score = _score(match_score)
            if score is None:
                score = job["match_score"]
            cursor = connection.execute(
                """
                INSERT INTO applications (
                    job_pk, job_id, company, job_title, job_url, match_score,
                    resume_version, application_date, status, notes,
                    review_json, needs_user_confirmation, required_field_missing,
                    is_demo, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, '{}', '[]', 1, ?, ?, ?)
                """,
                (
                    job_pk,
                    job["job_id"],
                    job["company"],
                    job["title"],
                    job["job_url"],
                    score,
                    str(resume_version or "").strip(),
                    now,
                    str(notes or "").strip(),
                    int(bool(is_demo)),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM applications WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
        return self._decode_application(row), True

    def create_application(self, job_pk: int, **kwargs: Any) -> dict[str, Any]:
        application, _ = self.get_or_create_application(job_pk, **kwargs)
        return application

    def get_application(self, application_id: int) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM applications WHERE id=?", (application_id,)
            ).fetchone()
        return self._decode_application(row) if row else None

    def get_application_for_job(self, job_pk: int) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM applications WHERE job_pk=?", (job_pk,)
            ).fetchone()
        return self._decode_application(row) if row else None

    def get_or_create_queue_item(
        self, application_id: int, *, max_retries: int = 3
    ) -> tuple[dict[str, Any], bool]:
        max_retries = int(max_retries)
        if not 0 <= max_retries <= 20:
            raise ValueError("max_retries must be from 0 to 20")
        now = utc_now()
        with self.transaction() as connection:
            application = connection.execute(
                "SELECT * FROM applications WHERE id=?", (application_id,)
            ).fetchone()
            if not application:
                raise RecordNotFound(f"application {application_id} does not exist")
            existing = connection.execute(
                "SELECT * FROM application_queue WHERE application_id=?",
                (application_id,),
            ).fetchone()
            if existing:
                return self._decode_queue(existing), False
            cursor = connection.execute(
                """
                INSERT INTO application_queue (
                    application_id, status, review_json, needs_user_confirmation,
                    required_field_missing, retry_count, max_retries,
                    created_at, updated_at
                ) VALUES (?, 'pending', '{}', '[]', 1, 0, ?, ?, ?)
                """,
                (application_id, max_retries, now, now),
            )
            row = connection.execute(
                "SELECT * FROM application_queue WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
        return self._decode_queue(row), True

    def enqueue_application(self, application_id: int, **kwargs: Any) -> dict[str, Any]:
        queue_item, _ = self.get_or_create_queue_item(application_id, **kwargs)
        return queue_item

    def get_queue_item(self, queue_id: int) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM application_queue WHERE id=?", (queue_id,)
            ).fetchone()
        return self._decode_queue(row) if row else None

    def list_queue(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        if status is not None and status not in QUEUE_STATUSES:
            raise ValueError(f"unknown queue status: {status}")
        if not 1 <= int(limit) <= 1000:
            raise ValueError("limit must be from 1 to 1000")
        where = "WHERE q.status=?" if status else ""
        params: tuple[Any, ...] = (status, int(limit)) if status else (int(limit),)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT q.*, a.company, a.job_title, a.job_url, a.match_score,
                       a.resume_version, a.is_demo, a.job_pk
                FROM application_queue q
                JOIN applications a ON a.id=q.application_id
                {where}
                ORDER BY q.created_at, q.id
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._decode_queue(row) for row in rows]

    def set_queue_review(
        self,
        queue_id: int,
        review: Any,
        *,
        needs_user_confirmation: list[Any] | tuple[Any, ...] | None = None,
        required_field_missing: bool | None = None,
    ) -> dict[str, Any]:
        review_data = _to_mapping(review) if not isinstance(review, Mapping) else dict(review)
        if needs_user_confirmation is None:
            candidate = review_data.get("needs_user_confirmation", [])
            needs = list(candidate) if isinstance(candidate, (list, tuple)) else []
        else:
            needs = list(needs_user_confirmation)
        if required_field_missing is None:
            missing_value = review_data.get("required_field_missing", False)
            required_missing = bool(missing_value)
            if review_data.get("can_submit") is False:
                required_missing = True
        else:
            required_missing = bool(required_field_missing)
        now = utc_now()
        review_json = _json(review_data, fallback={})
        needs_json = _json(needs, fallback=[])
        with self.transaction() as connection:
            queue = connection.execute(
                "SELECT * FROM application_queue WHERE id=?", (queue_id,)
            ).fetchone()
            if not queue:
                raise RecordNotFound(f"queue item {queue_id} does not exist")
            if queue["status"] in {"submitted", "skipped"}:
                raise InvalidStateTransition("cannot change review for a terminal queue item")
            connection.execute(
                """
                UPDATE application_queue SET review_json=?,
                    needs_user_confirmation=?, required_field_missing=?,
                    reviewed_at=?, updated_at=? WHERE id=?
                """,
                (
                    review_json,
                    needs_json,
                    int(required_missing),
                    now,
                    now,
                    queue_id,
                ),
            )
            connection.execute(
                """
                UPDATE applications SET review_json=?, needs_user_confirmation=?,
                    required_field_missing=?, updated_at=? WHERE id=?
                """,
                (
                    review_json,
                    needs_json,
                    int(required_missing),
                    now,
                    queue["application_id"],
                ),
            )
            row = connection.execute(
                "SELECT * FROM application_queue WHERE id=?", (queue_id,)
            ).fetchone()
        return self._decode_queue(row)

    update_review = set_queue_review

    def transition_queue(
        self,
        queue_id: int,
        new_status: str,
        *,
        user_confirmed_submit: bool = False,
        error: str = "",
        error_screenshot: str = "",
    ) -> dict[str, Any]:
        new_status = str(new_status).strip()
        if new_status not in QUEUE_STATUSES:
            raise ValueError(f"unknown queue status: {new_status}")
        now = utc_now()
        with self.transaction() as connection:
            queue = connection.execute(
                "SELECT * FROM application_queue WHERE id=?", (queue_id,)
            ).fetchone()
            if not queue:
                raise RecordNotFound(f"queue item {queue_id} does not exist")
            current = str(queue["status"])
            if new_status == current:
                return self._decode_queue(queue)
            if new_status not in QUEUE_TRANSITIONS[current]:
                raise InvalidStateTransition(
                    f"queue status cannot change from {current} to {new_status}"
                )
            if new_status == "ready_to_submit":
                needs = _load_json(queue["needs_user_confirmation"], [])
                if not queue["reviewed_at"]:
                    raise ReviewRequired("application review is required before submission")
                if bool(queue["required_field_missing"]) or bool(needs):
                    raise ReviewRequired(
                        "required fields or user confirmations remain unresolved"
                    )
            if new_status == "submitted" and not user_confirmed_submit:
                raise SubmissionConfirmationRequired(
                    "explicit user confirmation is required before final submit"
                )

            retry_count = int(queue["retry_count"])
            if new_status == "failed":
                retry_count += 1
            started_at = queue["started_at"]
            if new_status == "opening" and not started_at:
                started_at = now
            completed_at = queue["completed_at"]
            if new_status in {"submitted", "skipped"}:
                completed_at = now
            last_error = str(error or "").strip() if new_status == "failed" else ""
            screenshot = (
                str(error_screenshot or "").strip() if new_status == "failed" else ""
            )
            connection.execute(
                """
                UPDATE application_queue SET status=?, retry_count=?,
                    last_error=?, error_screenshot=?, started_at=?, completed_at=?,
                    updated_at=? WHERE id=? AND status=?
                """,
                (
                    new_status,
                    retry_count,
                    last_error,
                    screenshot,
                    started_at,
                    completed_at,
                    now,
                    queue_id,
                    current,
                ),
            )
            connection.execute(
                """
                UPDATE applications SET status=?, submitted_at=?, updated_at=?
                WHERE id=?
                """,
                (
                    new_status,
                    now if new_status == "submitted" else None,
                    now,
                    queue["application_id"],
                ),
            )
            row = connection.execute(
                "SELECT * FROM application_queue WHERE id=?", (queue_id,)
            ).fetchone()
        return self._decode_queue(row)

    update_queue_status = transition_queue

    def reset_legacy_autofill_placeholders(self) -> int:
        """Reset the old UI's synthetic "官网最终检查" queue state.

        Versions before the browser-event monitor marked every launched job as
        waiting before inspecting the page.  Only that exact synthetic shape is
        migrated; real browser blockers and user-entered reviews are untouched.
        """

        now = utc_now()
        reset: list[tuple[int, int]] = []
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT id, application_id, review_json, needs_user_confirmation
                FROM application_queue WHERE status='waiting_user'
                """
            ).fetchall()
            for row in rows:
                review = _load_json(row["review_json"], {})
                needs = _load_json(row["needs_user_confirmation"], [])
                if not isinstance(review, Mapping) or not isinstance(needs, list):
                    continue
                synthetic = (
                    review.get("mode") == "visible_official_browser"
                    and not review.get("last_browser_event")
                    and len(needs) == 1
                    and isinstance(needs[0], Mapping)
                    and needs[0].get("field_name") == "官网最终检查"
                )
                if synthetic:
                    reset.append((int(row["id"]), int(row["application_id"])))
            for queue_id, application_id in reset:
                connection.execute(
                    """
                    UPDATE application_queue SET status='pending', review_json='{}',
                        needs_user_confirmation='[]', required_field_missing=1,
                        reviewed_at=NULL, last_error='', error_screenshot='',
                        completed_at=NULL, updated_at=? WHERE id=?
                    """,
                    (now, queue_id),
                )
                connection.execute(
                    """
                    UPDATE applications SET status='pending', review_json='{}',
                        needs_user_confirmation='[]', required_field_missing=1,
                        submitted_at=NULL, updated_at=? WHERE id=?
                    """,
                    (now, application_id),
                )
        return len(reset)

    def retry_failed(self, queue_id: int) -> dict[str, Any]:
        now = utc_now()
        with self.transaction() as connection:
            queue = connection.execute(
                "SELECT * FROM application_queue WHERE id=?", (queue_id,)
            ).fetchone()
            if not queue:
                raise RecordNotFound(f"queue item {queue_id} does not exist")
            if queue["status"] != "failed":
                raise InvalidStateTransition("only failed queue items can be retried")
            if int(queue["retry_count"]) >= int(queue["max_retries"]):
                raise RetryLimitExceeded("application retry limit has been reached")
            connection.execute(
                """
                UPDATE application_queue SET status='pending', last_error='',
                    error_screenshot='', review_json='{}',
                    needs_user_confirmation='[]', required_field_missing=1,
                    reviewed_at=NULL, completed_at=NULL, updated_at=? WHERE id=?
                """,
                (now, queue_id),
            )
            connection.execute(
                """
                UPDATE applications SET status='pending', review_json='{}',
                    needs_user_confirmation='[]', required_field_missing=1,
                    submitted_at=NULL, updated_at=? WHERE id=?
                """,
                (now, queue["application_id"]),
            )
            row = connection.execute(
                "SELECT * FROM application_queue WHERE id=?", (queue_id,)
            ).fetchone()
        return self._decode_queue(row)

    def dashboard_counts(self, *, recommended_threshold: int = 70) -> dict[str, Any]:
        threshold = _score(recommended_threshold)
        assert threshold is not None
        with closing(self._connect()) as connection:
            total_jobs = int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
            recommended = int(
                connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE match_score>=?", (threshold,)
                ).fetchone()[0]
            )
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM application_queue GROUP BY status"
            ).fetchall()
            today_jobs = int(
                connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE date(discovered_at)=date('now')"
                ).fetchone()[0]
            )
            today_applications = int(
                connection.execute(
                    "SELECT COUNT(*) FROM applications WHERE date(application_date)=date('now')"
                ).fetchone()[0]
            )
            needs_confirmation = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM application_queue
                    WHERE status='waiting_user'
                       OR needs_user_confirmation NOT IN ('', '[]')
                    """
                ).fetchone()[0]
            )
        per_status = {status: 0 for status in QUEUE_STATUSES}
        per_status.update({str(row["status"]): int(row["count"]) for row in rows})
        active_pending = sum(
            per_status[name]
            for name in ("pending", "opening", "filling", "ready_to_submit")
        )
        return {
            "total_jobs": total_jobs,
            "recommended_jobs": recommended,
            "pending": active_pending,
            "submitted": per_status["submitted"],
            "failed": per_status["failed"],
            "needs_user_confirmation": needs_confirmation,
            "needs_human": needs_confirmation,
            "today_jobs": today_jobs,
            "today_applications": today_applications,
            "queue_by_status": per_status,
        }

    @staticmethod
    def _decode_job(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            raise RecordNotFound("job does not exist")
        result = dict(row)
        result["match"] = _load_json(result.get("match_json"), {})
        result["raw_job"] = _load_json(result.get("raw_json"), {})
        return result

    @staticmethod
    def _decode_application(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            raise RecordNotFound("application does not exist")
        result = dict(row)
        result["review"] = _load_json(result.get("review_json"), {})
        result["needs_user_confirmation"] = _load_json(
            result.get("needs_user_confirmation"), []
        )
        result["required_field_missing"] = bool(result["required_field_missing"])
        result["is_demo"] = bool(result["is_demo"])
        return result

    @staticmethod
    def _decode_queue(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            raise RecordNotFound("queue item does not exist")
        result = dict(row)
        result["review"] = _load_json(result.get("review_json"), {})
        result["needs_user_confirmation"] = _load_json(
            result.get("needs_user_confirmation"), []
        )
        result["required_field_missing"] = bool(result["required_field_missing"])
        if "is_demo" in result:
            result["is_demo"] = bool(result["is_demo"])
        return result


# ``Database`` is the concise public name used by app.py, while the explicit
# alias remains useful to library users and in tracebacks.
Database = JobAgentDatabase


__all__ = [
    "Database",
    "JobAgentDatabase",
    "QUEUE_STATUSES",
    "QUEUE_TRANSITIONS",
    "DatabaseError",
    "RecordNotFound",
    "DuplicateApplicationError",
    "InvalidStateTransition",
    "ReviewRequired",
    "SubmissionConfirmationRequired",
    "RetryLimitExceeded",
    "canonicalize_job_url",
]
