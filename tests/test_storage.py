import pytest

from job_assistant.models import Job, MatchResult
from job_assistant.storage import ApplicationRepository


def test_repository_preserves_and_validates_human_review_status(tmp_path):
    repository = ApplicationRepository(tmp_path / "applications.db")
    job = Job("1", "Engineer", "Acme", "上海", "Python", "https://example.com/1", "test")
    result = MatchResult(job=job, score=88, eligible=True, reasons=("匹配",))

    repository.save_results([result])
    assert repository.list_all()[0]["status"] == "review"

    repository.update_status("test", "1", "confirmed")
    assert repository.list_all()[0]["status"] == "confirmed"

    with pytest.raises(ValueError, match="不允许"):
        repository.update_status("test", "1", "submitted")


def test_job_details_and_resume_route_are_persisted_without_reading_resume(tmp_path):
    repository = ApplicationRepository(tmp_path / "applications.db")
    job = Job(
        id="details-1",
        title="Senior Engineer",
        company="Global Acme",
        location="Remote",
        description="Build Python services and review system designs.",
        url="https://example.com/jobs/details-1",
        source="greenhouse",
        department="Engineering",
        salary_min=30_000,
        salary_max=45_000,
        currency="CNY",
        period="month",
        salary_text="30k-45k/月",
        language="en",
    )
    repository.save_results(
        [MatchResult(job=job, score=91, eligible=True, reasons=("技能匹配",))]
    )

    record = repository.get("greenhouse", "details-1")
    assert record is not None
    assert record["description"] == job.description
    assert record["department"] == "Engineering"
    assert record["job_language"] == "en"
    assert record["salary_min"] == 30_000
    assert record["salary_max"] == 45_000
    assert record["currency"] == "CNY"
    assert record["period"] == "month"
    assert record["salary_text"] == "30k-45k/月"
    assert record["resume_language"] == ""
    assert record["resume_path"] == ""
    assert record["company_foreign"] == 0

    # The file intentionally does not exist: routing must store metadata only and
    # must never attempt to open or inspect the resume contents.
    resume_path = tmp_path / "private" / "english-resume.pdf"
    repository.set_resume_route(
        "greenhouse",
        "details-1",
        "EN",
        resume_path,
        company_foreign=True,
    )
    routed = repository.get("greenhouse", "details-1")
    assert routed is not None
    assert routed["resume_language"] == "en"
    assert routed["resume_path"] == str(resume_path)
    assert routed["company_foreign"] == 1

    # Refreshing the same job updates volatile job details but keeps the user's
    # explicit resume route and company classification.
    refreshed = Job(
        id="details-1",
        title="Senior Platform Engineer",
        company="Global Acme",
        location="Remote",
        description="Updated job description.",
        url="https://example.com/jobs/details-1",
        source="greenhouse",
        department="Platform",
        salary_min=32_000,
        salary_max=48_000,
        currency="CNY",
        period="month",
        salary_text="32k-48k/月",
        language="en",
    )
    repository.save_results([MatchResult(job=refreshed, score=94, eligible=True)])
    after_refresh = repository.get("greenhouse", "details-1")
    assert after_refresh is not None
    assert after_refresh["description"] == "Updated job description."
    assert after_refresh["salary_min"] == 32_000
    assert after_refresh["resume_language"] == "en"
    assert after_refresh["resume_path"] == str(resume_path)
    assert after_refresh["company_foreign"] == 1


def test_set_resume_route_requires_metadata_and_existing_job(tmp_path):
    repository = ApplicationRepository(tmp_path / "applications.db")
    job = Job("1", "Engineer", "Acme", "上海", "Python", "https://example.com/1", "test")
    repository.save_results([MatchResult(job=job, score=88, eligible=True)])

    with pytest.raises(ValueError, match="语言"):
        repository.set_resume_route("test", "1", "", tmp_path / "resume.pdf")
    with pytest.raises(ValueError, match="路径"):
        repository.set_resume_route("test", "1", "zh", "")
    with pytest.raises(KeyError, match="不存在"):
        repository.set_resume_route("test", "missing", "zh", tmp_path / "resume.pdf")


def test_approved_tailored_resume_survives_job_refresh_until_user_changes_route(tmp_path):
    repository = ApplicationRepository(tmp_path / "applications.db")
    job = Job("1", "BI Intern", "Acme", "北京", "Python SQL", "https://example.com/1", "test")
    repository.save_results([MatchResult(job=job, score=88, eligible=True)])
    tailored_path = tmp_path / "private" / "tailored.docx"

    repository.approve_tailored_resume(
        "test",
        "1",
        "zh",
        tailored_path,
        changes="前置数据分析经历",
        gaps="缺少 SQL 证据",
    )
    repository.save_results([MatchResult(job=job, score=90, eligible=True)])
    record = repository.get("test", "1")

    assert record is not None
    assert record["resume_path"] == str(tailored_path)
    assert record["resume_tailored"] == 1
    assert record["tailoring_summary"] == "前置数据分析经历"
    assert record["tailoring_gaps"] == "缺少 SQL 证据"
    assert record["tailoring_approved_at"]

    repository.set_resume_route("test", "1", "en", tmp_path / "base.pdf", True)
    base_record = repository.get("test", "1")
    assert base_record["resume_tailored"] == 0
    assert base_record["tailoring_summary"] == ""


def test_submitted_requires_evidence_and_follow_up_is_tied_to_exact_job(tmp_path):
    repository = ApplicationRepository(tmp_path / "applications.db")
    first = Job("1", "Engineer", "Acme", "上海", "Python", "https://example.com/1", "test")
    second = Job("2", "Analyst", "Acme", "上海", "SQL", "https://example.com/2", "test")
    repository.save_results(
        [
            MatchResult(job=first, score=88, eligible=True),
            MatchResult(job=second, score=70, eligible=True),
        ]
    )
    repository.update_status("test", "1", "confirmed")
    repository.update_status("test", "1", "opened")

    with pytest.raises(ValueError, match="提交证据"):
        repository.update_status("test", "1", "submitted")

    repository.update_status(
        "test", "1", "submitted", submission_evidence="Thank-you page #ABC123"
    )
    follow_up_id = repository.add_follow_up(
        "test",
        "1",
        event_date="2099-01-02",
        event_time="09:30",
        event_type="一轮面试",
    )

    record = repository.get("test", "1")
    assert record["submission_evidence"] == "Thank-you page #ABC123"
    assert record["current_stage"] == "一轮面试"
    follow_up = repository.list_follow_ups()[0]
    assert follow_up["id"] == follow_up_id
    assert follow_up["job_id"] == "1"
    assert follow_up["title"] == "Engineer"

    with pytest.raises(ValueError, match="只能为已投递"):
        repository.add_follow_up(
            "test", "2", event_date="2099-01-03", event_type="错误关联"
        )


def test_existing_database_is_migrated_without_losing_rows(tmp_path):
    import sqlite3

    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE applications (
                source TEXT NOT NULL, job_id TEXT NOT NULL, title TEXT NOT NULL,
                company TEXT NOT NULL, location TEXT NOT NULL, url TEXT NOT NULL,
                score INTEGER NOT NULL, eligible INTEGER NOT NULL, reasons TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'review',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source, job_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO applications
                (source, job_id, title, company, location, url, score, eligible, reasons)
            VALUES ('legacy', '1', 'Engineer', 'Acme', '上海', 'https://example.com', 80, 1, 'ok')
            """
        )

    repository = ApplicationRepository(database)
    record = repository.get("legacy", "1")
    assert record["title"] == "Engineer"
    assert record["submission_evidence"] == ""
    assert record["notes"] == ""
    assert record["description"] == ""
    assert record["department"] == ""
    assert record["job_language"] == ""
    assert record["salary_min"] is None
    assert record["salary_max"] is None
    assert record["currency"] == ""
    assert record["period"] == ""
    assert record["salary_text"] == ""
    assert record["resume_language"] == ""
    assert record["resume_path"] == ""
    assert record["resume_tailored"] == 0
    assert record["tailoring_summary"] == ""
    assert record["tailoring_gaps"] == ""
    assert record["tailoring_approved_at"] == ""
    assert record["company_foreign"] == 0
