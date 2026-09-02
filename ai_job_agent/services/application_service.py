from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ..database import (
    Database,
    DuplicateApplicationError,
    RecordNotFound,
)


class ApplicationService:
    """Orchestrate jobs, application records, and the safe submission queue.

    This service performs no browser clicks itself.  A Playwright worker advances
    the queue and supplies its review here; therefore final submission always
    remains a separate, explicit call with ``user_confirmed=True``.
    """

    def __init__(
        self,
        database: Database | str | Path = "data/app.db",
        *,
        demo_mode: bool = False,
        auto_submit: bool = False,
    ) -> None:
        self.database = database if isinstance(database, Database) else Database(database)
        self.demo_mode = bool(demo_mode)
        # Kept as configuration metadata for the UI.  The MVP still requires an
        # action-time confirmation even when callers accidentally set this true.
        self.auto_submit = bool(auto_submit)

    def register_job(self, job: Any, assessment: Any | None = None) -> dict[str, Any]:
        return self.database.upsert_job(job, assessment)

    save_job = register_job
    upsert_job = register_job

    def enqueue_job(
        self,
        job: int | Any,
        *,
        assessment: Any | None = None,
        resume_version: str = "",
        notes: str = "",
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Add one job to the application queue, idempotently.

        ``job`` can be an internal integer ID or a JobPosting/dict.  Repeated
        clicks return the existing queue record.  A submitted record is rejected
        so a browser worker cannot submit it twice.
        """

        if isinstance(job, int) and not isinstance(job, bool):
            stored_job = self.database.get_job(job)
            if stored_job is None:
                raise RecordNotFound(f"job {job} does not exist")
            if assessment is not None:
                stored_job = self.database.upsert_job(stored_job, assessment)
        else:
            stored_job = self.register_job(job, assessment)
        application, application_created = self.database.get_or_create_application(
            int(stored_job["id"]),
            match_score=stored_job.get("match_score"),
            resume_version=resume_version,
            notes=notes,
            is_demo=self.demo_mode,
        )
        if application["status"] == "submitted":
            raise DuplicateApplicationError(
                f"{application['company']} / {application['job_title']} was already submitted"
            )
        queue, queue_created = self.database.get_or_create_queue_item(
            int(application["id"]), max_retries=max_retries
        )
        if queue["status"] == "submitted":
            raise DuplicateApplicationError(
                f"{application['company']} / {application['job_title']} was already submitted"
            )
        result = dict(queue)
        result["application"] = application
        result["job"] = stored_job
        result["already_exists"] = not (application_created or queue_created)
        return result

    prepare_application = enqueue_job

    def create_application(
        self,
        job: int | Any,
        *,
        assessment: Any | None = None,
        resume_version: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        """Create or return the application record without adding another queue row."""

        if isinstance(job, int) and not isinstance(job, bool):
            stored_job = self.database.get_job(job)
            if stored_job is None:
                raise RecordNotFound(f"job {job} does not exist")
            if assessment is not None:
                stored_job = self.database.upsert_job(stored_job, assessment)
        else:
            stored_job = self.register_job(job, assessment)
        application, _ = self.database.get_or_create_application(
            int(stored_job["id"]),
            match_score=stored_job.get("match_score"),
            resume_version=resume_version,
            notes=notes,
            is_demo=self.demo_mode,
        )
        return application

    def enqueue_application(
        self, application_id: int, *, max_retries: int = 3
    ) -> dict[str, Any]:
        """Queue an existing application record, idempotently."""

        application = self.database.get_application(application_id)
        if application is None:
            raise RecordNotFound(f"application {application_id} does not exist")
        if application["status"] == "submitted":
            raise DuplicateApplicationError(
                f"{application['company']} / {application['job_title']} was already submitted"
            )
        queue, created = self.database.get_or_create_queue_item(
            application_id, max_retries=max_retries
        )
        if queue["status"] == "submitted":
            raise DuplicateApplicationError(
                f"{application['company']} / {application['job_title']} was already submitted"
            )
        result = dict(queue)
        result["application"] = application
        result["already_exists"] = not created
        return result

    def transition(
        self,
        queue_id: int,
        status: str,
        *,
        error: str = "",
        error_screenshot: str = "",
        user_confirmed: bool = False,
    ) -> dict[str, Any]:
        return self.database.transition_queue(
            queue_id,
            status,
            user_confirmed_submit=user_confirmed,
            error=error,
            error_screenshot=error_screenshot,
        )

    update_status = transition

    def record_review(
        self,
        queue_id: int,
        review: Any,
        *,
        needs_user_confirmation: list[Any] | tuple[Any, ...] | None = None,
        required_field_missing: bool | None = None,
    ) -> dict[str, Any]:
        return self.database.set_queue_review(
            queue_id,
            review,
            needs_user_confirmation=needs_user_confirmation,
            required_field_missing=required_field_missing,
        )

    update_review = record_review

    def resolve_user_confirmations(
        self,
        queue_id: int,
        resolved_fields: Iterable[str] | None = None,
        *,
        review: Mapping[str, Any] | None = None,
        required_field_missing: bool = False,
    ) -> dict[str, Any]:
        """Mark prompts as handled without persisting the user's sensitive answers."""

        current = self.database.get_queue_item(queue_id)
        if current is None:
            raise RecordNotFound(f"queue item {queue_id} does not exist")
        existing_needs = list(current["needs_user_confirmation"])
        if resolved_fields is None:
            remaining: list[Any] = []
        else:
            normalized = {str(item).strip().casefold() for item in resolved_fields}

            def identity(item: Any) -> str:
                if isinstance(item, Mapping):
                    for key in ("field", "mapped_field", "label", "question"):
                        if item.get(key):
                            return str(item[key]).strip().casefold()
                return str(item).strip().casefold()

            remaining = [item for item in existing_needs if identity(item) not in normalized]
        review_data = dict(review) if review is not None else dict(current["review"])
        return self.record_review(
            queue_id,
            review_data,
            needs_user_confirmation=remaining,
            required_field_missing=required_field_missing,
        )

    def mark_ready_to_submit(self, queue_id: int) -> dict[str, Any]:
        return self.transition(queue_id, "ready_to_submit")

    def confirm_submission(
        self, queue_id: int, *, user_confirmed: bool
    ) -> dict[str, Any]:
        """Record the final confirmed submit (or simulated submit in Demo Mode).

        This method never manipulates the browser.  The caller invokes it only
        after Playwright has submitted in real mode, or after the demo worker has
        simulated that step.
        """

        return self.transition(
            queue_id, "submitted", user_confirmed=bool(user_confirmed)
        )

    def fail(
        self,
        queue_id: int,
        error: str,
        *,
        screenshot: str = "",
    ) -> dict[str, Any]:
        return self.transition(
            queue_id,
            "failed",
            error=error,
            error_screenshot=screenshot,
        )

    def retry_failed(self, queue_id: int) -> dict[str, Any]:
        return self.database.retry_failed(queue_id)

    retry = retry_failed

    def reset_legacy_autofill_placeholders(self) -> int:
        return self.database.reset_legacy_autofill_placeholders()

    def get_queue_item(self, queue_id: int) -> dict[str, Any] | None:
        return self.database.get_queue_item(queue_id)

    def list_queue(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return self.database.list_queue(status=status, limit=limit)

    def dashboard_counts(self, *, recommended_threshold: int = 70) -> dict[str, Any]:
        return self.database.dashboard_counts(
            recommended_threshold=recommended_threshold
        )


__all__ = ["ApplicationService"]
