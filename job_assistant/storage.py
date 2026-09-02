from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .models import MatchResult

ALLOWED_TRANSITIONS = {
    "review": {"confirmed", "skipped"},
    "confirmed": {"opened", "blocked", "skipped"},
    "opened": {"submitted", "blocked", "skipped"},
    "blocked": {"confirmed", "opened", "skipped"},
    "submitted": {"offer", "rejected"},
    "offer": set(),
    "rejected": set(),
    "skipped": set(),
}

FOLLOW_UP_STATUSES = {"scheduled", "completed", "cancelled"}
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")


class ApplicationRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS applications (
                    source TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    department TEXT NOT NULL DEFAULT '',
                    job_language TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL,
                    salary_min REAL,
                    salary_max REAL,
                    currency TEXT NOT NULL DEFAULT '',
                    period TEXT NOT NULL DEFAULT '',
                    salary_text TEXT NOT NULL DEFAULT '',
                    score INTEGER NOT NULL,
                    eligible INTEGER NOT NULL,
                    reasons TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'review',
                    resume_language TEXT NOT NULL DEFAULT '',
                    resume_path TEXT NOT NULL DEFAULT '',
                    company_foreign INTEGER NOT NULL DEFAULT 0,
                    submission_evidence TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    next_action TEXT NOT NULL DEFAULT '',
                    current_stage TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (source, job_id)
                )
                """
            )
            self._migrate_application_columns(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS follow_ups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    event_time TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'scheduled',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (source, job_id)
                        REFERENCES applications(source, job_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_follow_ups_date
                ON follow_ups(event_date, event_time)
                """
            )

    @staticmethod
    def _migrate_application_columns(connection: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(applications)").fetchall()
        }
        additions = {
            "description": "TEXT NOT NULL DEFAULT ''",
            "department": "TEXT NOT NULL DEFAULT ''",
            "job_language": "TEXT NOT NULL DEFAULT ''",
            "salary_min": "REAL",
            "salary_max": "REAL",
            "currency": "TEXT NOT NULL DEFAULT ''",
            "period": "TEXT NOT NULL DEFAULT ''",
            "salary_text": "TEXT NOT NULL DEFAULT ''",
            "resume_language": "TEXT NOT NULL DEFAULT ''",
            "resume_path": "TEXT NOT NULL DEFAULT ''",
            "resume_tailored": "INTEGER NOT NULL DEFAULT 0",
            "tailoring_summary": "TEXT NOT NULL DEFAULT ''",
            "tailoring_gaps": "TEXT NOT NULL DEFAULT ''",
            "tailoring_approved_at": "TEXT NOT NULL DEFAULT ''",
            "company_foreign": "INTEGER NOT NULL DEFAULT 0",
            "submission_evidence": "TEXT NOT NULL DEFAULT ''",
            "notes": "TEXT NOT NULL DEFAULT ''",
            "next_action": "TEXT NOT NULL DEFAULT ''",
            "current_stage": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in additions.items():
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE applications ADD COLUMN {name} {definition}"
                )

    def save_results(self, results: list[MatchResult]) -> None:
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO applications
                    (source, job_id, title, company, location, description,
                     department, job_language, url, salary_min, salary_max,
                     currency, period, salary_text, score, eligible, reasons)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, job_id) DO UPDATE SET
                    title=excluded.title,
                    company=excluded.company,
                    location=excluded.location,
                    description=excluded.description,
                    department=excluded.department,
                    job_language=excluded.job_language,
                    url=excluded.url,
                    salary_min=excluded.salary_min,
                    salary_max=excluded.salary_max,
                    currency=excluded.currency,
                    period=excluded.period,
                    salary_text=excluded.salary_text,
                    score=excluded.score,
                    eligible=excluded.eligible,
                    reasons=excluded.reasons,
                    updated_at=CURRENT_TIMESTAMP
                """,
                [
                    (
                        result.job.source,
                        result.job.id,
                        result.job.title,
                        result.job.company,
                        result.job.location,
                        result.job.description,
                        result.job.department,
                        result.job.language,
                        result.job.url,
                        result.job.salary_min,
                        result.job.salary_max,
                        result.job.currency,
                        result.job.period,
                        result.job.salary_text,
                        result.score,
                        int(result.eligible),
                        "；".join(result.reasons),
                    )
                    for result in results
                ],
            )

    def set_resume_route(
        self,
        source: str,
        job_id: str,
        language: str,
        path: str | Path,
        company_foreign: bool = False,
    ) -> None:
        """Record which local resume should be used without opening the file."""
        normalized_language = str(language).strip().casefold()
        normalized_path = str(path).strip()
        if not normalized_language:
            raise ValueError("简历语言不能为空")
        if not normalized_path:
            raise ValueError("简历路径不能为空")

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE applications
                SET resume_language=?, resume_path=?, company_foreign=?,
                    resume_tailored=0, tailoring_summary='', tailoring_gaps='',
                    tailoring_approved_at='',
                    updated_at=CURRENT_TIMESTAMP
                WHERE source=? AND job_id=?
                """,
                (
                    normalized_language,
                    normalized_path,
                    int(bool(company_foreign)),
                    source,
                    job_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError("投递记录不存在")

    def approve_tailored_resume(
        self,
        source: str,
        job_id: str,
        language: str,
        path: str | Path,
        *,
        changes: str,
        gaps: str,
        company_foreign: bool = False,
    ) -> None:
        """Bind a user-approved per-job draft without changing the base profile."""

        normalized_language = str(language).strip().casefold()
        normalized_path = str(path).strip()
        if normalized_language not in {"zh", "en"}:
            raise ValueError("定制简历语言必须是 zh 或 en")
        if not normalized_path:
            raise ValueError("定制简历路径不能为空")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE applications
                SET resume_language=?, resume_path=?, company_foreign=?,
                    resume_tailored=1, tailoring_summary=?, tailoring_gaps=?,
                    tailoring_approved_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE source=? AND job_id=?
                """,
                (
                    normalized_language,
                    normalized_path,
                    int(bool(company_foreign)),
                    str(changes).strip(),
                    str(gaps).strip(),
                    source,
                    job_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError("投递记录不存在")

    def get(self, source: str, job_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM applications WHERE source=? AND job_id=?",
                (source, job_id),
            ).fetchone()
        return dict(row) if row else None

    def update_status(
        self,
        source: str,
        job_id: str,
        new_status: str,
        *,
        submission_evidence: str = "",
    ) -> None:
        if new_status not in ALLOWED_TRANSITIONS:
            raise ValueError(f"未知投递状态：{new_status}")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM applications WHERE source=? AND job_id=?",
                (source, job_id),
            ).fetchone()
            if row is None:
                raise KeyError("投递记录不存在")
            current = str(row["status"])
            if current == new_status:
                return
            if new_status not in ALLOWED_TRANSITIONS.get(current, set()):
                raise ValueError(f"不允许从 {current} 变更为 {new_status}")
            evidence = submission_evidence.strip()
            if new_status == "submitted" and not evidence:
                raise ValueError("标记为已投递前必须填写提交证据")
            connection.execute(
                """
                UPDATE applications
                SET status=?,
                    submission_evidence=CASE WHEN ?='' THEN submission_evidence ELSE ? END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE source=? AND job_id=?
                """,
                (new_status, evidence, evidence, source, job_id),
            )

    def update_details(
        self,
        source: str,
        job_id: str,
        *,
        notes: str,
        next_action: str,
        current_stage: str,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE applications
                SET notes=?, next_action=?, current_stage=?, updated_at=CURRENT_TIMESTAMP
                WHERE source=? AND job_id=?
                """,
                (
                    notes.strip(),
                    next_action.strip(),
                    current_stage.strip(),
                    source,
                    job_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError("投递记录不存在")

    def list_all(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM applications ORDER BY updated_at DESC, score DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def add_follow_up(
        self,
        source: str,
        job_id: str,
        *,
        event_date: str,
        event_time: str = "",
        event_type: str,
        notes: str = "",
    ) -> int:
        normalized_date = event_date.strip()
        normalized_time = event_time.strip()
        normalized_type = event_type.strip()
        if not DATE_PATTERN.fullmatch(normalized_date):
            raise ValueError("日期必须使用 YYYY-MM-DD 格式")
        try:
            datetime.strptime(normalized_date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("日期无效") from exc
        if normalized_time:
            if not TIME_PATTERN.fullmatch(normalized_time):
                raise ValueError("时间必须使用 HH:MM 格式")
            try:
                datetime.strptime(normalized_time, "%H:%M")
            except ValueError as exc:
                raise ValueError("时间无效") from exc
        if not normalized_type:
            raise ValueError("请填写日程内容")

        with self._connect() as connection:
            application = connection.execute(
                "SELECT status FROM applications WHERE source=? AND job_id=?",
                (source, job_id),
            ).fetchone()
            if application is None:
                raise KeyError("投递记录不存在")
            if str(application["status"]) != "submitted":
                raise ValueError("只能为已投递岗位创建日程")
            cursor = connection.execute(
                """
                INSERT INTO follow_ups
                    (source, job_id, event_date, event_time, event_type, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    job_id,
                    normalized_date,
                    normalized_time,
                    normalized_type,
                    notes.strip(),
                ),
            )
            connection.execute(
                """
                UPDATE applications
                SET current_stage=?, next_action=?, updated_at=CURRENT_TIMESTAMP
                WHERE source=? AND job_id=?
                """,
                (normalized_type, f"{normalized_date} {normalized_time}".strip(), source, job_id),
            )
            return int(cursor.lastrowid)

    def list_follow_ups(self, *, upcoming_only: bool = False) -> list[dict[str, object]]:
        query = """
            SELECT f.*, a.title, a.company, a.url
            FROM follow_ups AS f
            JOIN applications AS a
              ON a.source=f.source AND a.job_id=f.job_id
        """
        parameters: tuple[object, ...] = ()
        if upcoming_only:
            query += " WHERE f.event_date>=? AND f.status='scheduled'"
            parameters = (date.today().isoformat(),)
        query += " ORDER BY f.event_date ASC, f.event_time ASC, f.id ASC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def update_follow_up_status(self, follow_up_id: int, new_status: str) -> None:
        if new_status not in FOLLOW_UP_STATUSES:
            raise ValueError("未知日程状态")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE follow_ups
                SET status=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (new_status, int(follow_up_id)),
            )
            if cursor.rowcount != 1:
                raise KeyError("日程不存在")
