import sqlite3

import pytest

from ai_job_agent.database import (
    Database,
    DuplicateApplicationError,
    InvalidStateTransition,
    RetryLimitExceeded,
    ReviewRequired,
    SubmissionConfirmationRequired,
)
from ai_job_agent.services.application_service import ApplicationService


def sample_job(**overrides):
    job = {
        "job_id": "job-101",
        "title": "AI Agent Intern",
        "company": "Example Labs",
        "location": "上海",
        "job_type": "Internship",
        "department": "Engineering",
        "description": "Build safe Python agents.",
        "requirements": "Python, SQL",
        "job_url": "https://careers.example.com/jobs/101",
        "source": "generic",
        "publish_date": "2026-09-01",
    }
    job.update(overrides)
    return job


def assessment(score=92):
    return {
        "match_score": score,
        "match_level": "强烈推荐",
        "matched_skills": ["Python"],
        "missing_skills": ["LangGraph"],
        "advantages": ["Agent project"],
        "risks": [],
        "reason": "Evidence-backed match",
        "recommendation": "投递",
    }


def advance_to_filling(service, queue_id):
    service.transition(queue_id, "opening")
    return service.transition(queue_id, "filling")


def test_schema_and_backward_compatible_migration_preserve_job(tmp_path):
    database_path = tmp_path / "app.db"
    # Simulate an early jobs table which predates matching and raw JSON columns.
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                job_url TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO jobs(title, company, job_url)
            VALUES ('Legacy Analyst', 'Legacy Co', 'https://example.com/legacy')
            """
        )

    database = Database(database_path)
    legacy = database.get_job(1)
    assert legacy is not None
    assert legacy["title"] == "Legacy Analyst"
    assert legacy["canonical_url"] == "https://example.com/legacy"
    assert legacy["match"] == {}

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"jobs", "applications", "application_queue"} <= tables
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_jobs_are_deduplicated_by_canonical_url_and_external_id(tmp_path):
    database = Database(tmp_path / "app.db")
    first = database.upsert_job(sample_job(), assessment())
    same_url = database.upsert_job(
        sample_job(
            title="Renamed AI Intern",
            job_url="https://CAREERS.example.com/jobs/101/?utm_source=newsletter#apply",
        ),
        assessment(94),
    )
    same_id = database.upsert_job(
        sample_job(
            title="Final title",
            job_url="https://careers.example.com/job-detail/101",
        ),
        assessment(95),
    )

    assert same_url["id"] == first["id"]
    assert same_id["id"] == first["id"]
    assert database.list_jobs()[0]["title"] == "Final title"
    assert len(database.list_jobs()) == 1


def test_queue_is_idempotent_and_submitted_job_cannot_be_queued_again(tmp_path):
    service = ApplicationService(tmp_path / "app.db", demo_mode=True)
    first = service.enqueue_job(sample_job(), assessment=assessment())
    second = service.enqueue_job(sample_job(), assessment=assessment())

    assert first["id"] == second["id"]
    assert second["already_exists"] is True
    assert len(service.list_queue()) == 1

    advance_to_filling(service, first["id"])
    service.record_review(
        first["id"],
        {"required_field_missing": False, "can_submit": True},
        needs_user_confirmation=[],
    )
    service.mark_ready_to_submit(first["id"])
    service.confirm_submission(first["id"], user_confirmed=True)

    with pytest.raises(DuplicateApplicationError, match="already submitted"):
        service.enqueue_job(sample_job(), assessment=assessment())


def test_state_machine_requires_review_resolved_fields_and_submit_confirmation(tmp_path):
    service = ApplicationService(tmp_path / "app.db")
    queue = service.enqueue_job(sample_job(), assessment=assessment())

    with pytest.raises(InvalidStateTransition):
        service.transition(queue["id"], "filling")

    advance_to_filling(service, queue["id"])
    service.record_review(
        queue["id"],
        {"required_field_missing": False},
        needs_user_confirmation=[{"field": "expected_salary", "label": "期望薪资"}],
    )
    with pytest.raises(ReviewRequired, match="unresolved"):
        service.mark_ready_to_submit(queue["id"])

    service.resolve_user_confirmations(
        queue["id"], ["expected_salary"], required_field_missing=False
    )
    ready = service.mark_ready_to_submit(queue["id"])
    assert ready["status"] == "ready_to_submit"

    with pytest.raises(SubmissionConfirmationRequired, match="confirmation"):
        service.confirm_submission(queue["id"], user_confirmed=False)
    submitted = service.confirm_submission(queue["id"], user_confirmed=True)
    assert submitted["status"] == "submitted"


def test_review_is_required_even_when_form_has_no_unknown_fields(tmp_path):
    service = ApplicationService(tmp_path / "app.db")
    queue = service.enqueue_job(sample_job())
    service.transition(queue["id"], "opening")

    with pytest.raises(ReviewRequired, match="review"):
        service.mark_ready_to_submit(queue["id"])

    reviewed = service.record_review(
        queue["id"],
        {"required_field_missing": False, "checked_fields": ["name", "email"]},
    )
    assert reviewed["review"]["checked_fields"] == ["name", "email"]
    assert reviewed["needs_user_confirmation"] == []
    assert service.mark_ready_to_submit(queue["id"])["status"] == "ready_to_submit"


def test_failure_retry_limit_and_error_evidence(tmp_path):
    service = ApplicationService(tmp_path / "app.db")
    queue = service.enqueue_job(sample_job(), max_retries=2)

    service.transition(queue["id"], "opening")
    failed = service.fail(
        queue["id"], "selector changed", screenshot="logs/job-101-error.png"
    )
    assert failed["retry_count"] == 1
    assert failed["last_error"] == "selector changed"
    assert failed["error_screenshot"].endswith("job-101-error.png")

    pending = service.retry_failed(queue["id"])
    assert pending["status"] == "pending"
    assert pending["last_error"] == ""
    assert pending["review"] == {}

    service.transition(queue["id"], "opening")
    service.fail(queue["id"], "still unavailable")
    with pytest.raises(RetryLimitExceeded, match="limit"):
        service.retry_failed(queue["id"])


def test_dashboard_counts_report_statuses_and_human_attention(tmp_path):
    service = ApplicationService(tmp_path / "app.db")
    first = service.enqueue_job(sample_job(), assessment=assessment(92))
    second = service.enqueue_job(
        sample_job(
            job_id="job-102",
            title="Data Intern",
            job_url="https://careers.example.com/jobs/102",
        ),
        assessment=assessment(75),
    )
    service.transition(first["id"], "opening")
    service.transition(first["id"], "waiting_user")
    service.record_review(
        first["id"],
        {"required_field_missing": True},
        needs_user_confirmation=["work_authorization"],
    )
    service.transition(second["id"], "failed", error="network")

    counts = service.dashboard_counts()
    assert counts["total_jobs"] == 2
    assert counts["recommended_jobs"] == 2
    assert counts["failed"] == 1
    assert counts["needs_human"] == 1
    assert counts["queue_by_status"]["waiting_user"] == 1
    assert counts["queue_by_status"]["failed"] == 1


def test_only_legacy_synthetic_manual_placeholder_is_reset(tmp_path):
    service = ApplicationService(tmp_path / "app.db")
    legacy = service.enqueue_job(sample_job(), assessment=assessment())
    actual = service.enqueue_job(
        sample_job(
            job_id="job-actual",
            title="Actual blocker",
            job_url="https://careers.example.com/jobs/actual",
        ),
        assessment=assessment(),
    )
    for queue in (legacy, actual):
        service.transition(queue["id"], "opening")
        service.transition(queue["id"], "filling")
        service.transition(queue["id"], "waiting_user")

    service.record_review(
        legacy["id"],
        {"mode": "visible_official_browser", "will_submit": False},
        needs_user_confirmation=[
            {"field_name": "官网最终检查", "reason": "旧版固定占位"}
        ],
        required_field_missing=True,
    )
    service.record_review(
        actual["id"],
        {
            "mode": "visible_official_browser",
            "last_browser_event": "manual_action_required",
        },
        needs_user_confirmation=[
            {"field_name": "Work authorization", "reason": "官网真实留空项"}
        ],
        required_field_missing=True,
    )

    assert service.reset_legacy_autofill_placeholders() == 1
    assert service.get_queue_item(legacy["id"])["status"] == "pending"
    assert service.get_queue_item(legacy["id"])["review"] == {}
    assert service.get_queue_item(actual["id"])["status"] == "waiting_user"
    assert service.get_queue_item(actual["id"])["needs_user_confirmation"][0][
        "field_name"
    ] == "Work authorization"
