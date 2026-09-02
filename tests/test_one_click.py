from __future__ import annotations

from pathlib import Path

import pytest

from job_assistant.autofill import AutofillError
from job_assistant.models import Job, MatchResult
from job_assistant.one_click import prepare_and_start_one_click
from job_assistant.profile import ApplicantProfile, ProfileError
from job_assistant.resume_export import resume_text_to_docx
from job_assistant.storage import ApplicationRepository


class FakeProcess:
    pid = 4321


def _profile(*, english_resume: str = "resume.docx") -> ApplicantProfile:
    return ApplicantProfile(
        first_name="Test",
        last_name="Candidate",
        email="candidate@example.com",
        phone="13800000000",
        location="Shanghai, China",
        english_resume=english_resume,
    )


def _result(*, source: str = "Greenhouse:example", description: str = "Python and data analysis") -> MatchResult:
    return MatchResult(
        job=Job(
            id="job-1",
            title="Data Analyst Intern",
            company="Example",
            location="Shanghai",
            description=description,
            url="https://job-boards.greenhouse.io/example/jobs/123",
            source=source,
            language="en",
        ),
        score=82,
        eligible=True,
        reasons=("岗位匹配",),
    )


def _write_resume(directory: Path, name: str = "resume.docx") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(
        resume_text_to_docx(
            "CONTACT\nTest Candidate\nSKILLS\n- Python data analysis\nEDUCATION\n- BEng"
        )
    )
    return path


def test_one_click_prepares_tailored_resume_and_opens_without_submitting(tmp_path):
    resumes_dir = tmp_path / "resumes"
    base = _write_resume(resumes_dir)
    repository = ApplicationRepository(tmp_path / "applications.db")
    result = _result()
    repository.save_results([result])
    started = []

    outcome = prepare_and_start_one_click(
        repository,
        _profile(),
        result,
        planned_language="en",
        company_foreign=True,
        resumes_dir=resumes_dir,
        process_starter=lambda plan: started.append(plan) or FakeProcess(),
    )

    record = repository.get(result.job.source, result.job.id)
    assert record is not None
    assert record["status"] == "opened"
    assert record["status"] != "submitted"
    assert record["resume_tailored"] == 1
    assert Path(str(record["resume_path"])).is_file()
    assert Path(str(record["resume_path"])) != base
    assert outcome.tailored is True
    assert outcome.plan.public_summary()["will_submit"] is False
    assert started == [outcome.plan]


def test_one_click_reuses_approved_tailored_resume_without_clearing_metadata(tmp_path):
    resumes_dir = tmp_path / "resumes"
    tailored = _write_resume(resumes_dir, "tailored.docx")
    repository = ApplicationRepository(tmp_path / "applications.db")
    result = _result()
    repository.save_results([result])
    repository.approve_tailored_resume(
        result.job.source,
        result.job.id,
        "en",
        tailored,
        changes="前置数据分析经历",
        gaps="缺少 SQL 证据",
        company_foreign=True,
    )
    before = repository.get(result.job.source, result.job.id)

    outcome = prepare_and_start_one_click(
        repository,
        _profile(english_resume=""),
        result,
        planned_language="en",
        company_foreign=True,
        resumes_dir=resumes_dir,
        process_starter=lambda _plan: FakeProcess(),
    )

    after = repository.get(result.job.source, result.job.id)
    assert before is not None and after is not None
    assert outcome.reused_tailored is True
    assert after["resume_tailored"] == 1
    assert after["tailoring_summary"] == before["tailoring_summary"]
    assert after["tailoring_gaps"] == before["tailoring_gaps"]
    assert after["tailoring_approved_at"] == before["tailoring_approved_at"]


def test_one_click_browser_failure_never_marks_opened_or_submitted(tmp_path):
    resumes_dir = tmp_path / "resumes"
    _write_resume(resumes_dir)
    repository = ApplicationRepository(tmp_path / "applications.db")
    result = _result(description="")
    repository.save_results([result])

    def fail_to_start(_plan):
        raise OSError("browser unavailable")

    with pytest.raises(OSError, match="browser unavailable"):
        prepare_and_start_one_click(
            repository,
            _profile(),
            result,
            planned_language="en",
            company_foreign=True,
            resumes_dir=resumes_dir,
            process_starter=fail_to_start,
        )

    record = repository.get(result.job.source, result.job.id)
    assert record is not None
    assert record["status"] == "confirmed"
    assert record["status"] != "submitted"


def test_one_click_missing_english_resume_stops_before_browser_and_status_change(tmp_path):
    repository = ApplicationRepository(tmp_path / "applications.db")
    result = _result()
    repository.save_results([result])
    started = []

    with pytest.raises(ProfileError, match="英文简历"):
        prepare_and_start_one_click(
            repository,
            _profile(english_resume=""),
            result,
            planned_language="en",
            company_foreign=True,
            resumes_dir=tmp_path / "resumes",
            process_starter=lambda plan: started.append(plan) or FakeProcess(),
        )

    assert started == []
    assert repository.get(result.job.source, result.job.id)["status"] == "review"


def test_one_click_rejects_unknown_source_before_browser(tmp_path):
    resumes_dir = tmp_path / "resumes"
    _write_resume(resumes_dir)
    repository = ApplicationRepository(tmp_path / "applications.db")
    result = _result(source="other")
    repository.save_results([result])
    started = []

    with pytest.raises(AutofillError, match="Greenhouse"):
        prepare_and_start_one_click(
            repository,
            _profile(),
            result,
            planned_language="en",
            company_foreign=True,
            resumes_dir=resumes_dir,
            process_starter=lambda plan: started.append(plan) or FakeProcess(),
        )

    assert started == []
    assert repository.get(result.job.source, result.job.id)["status"] == "review"


@pytest.mark.parametrize("source", ("Lever:example", "Ashby:example", "SmartRecruiters:example"))
def test_one_click_accepts_supported_non_greenhouse_source(tmp_path, source):
    resumes_dir = tmp_path / "resumes"
    _write_resume(resumes_dir)
    repository = ApplicationRepository(tmp_path / "applications.db")
    result = _result(source=source, description="")
    repository.save_results([result])
    started = []

    outcome = prepare_and_start_one_click(
        repository,
        _profile(),
        result,
        planned_language="en",
        company_foreign=True,
        resumes_dir=resumes_dir,
        process_starter=lambda plan: started.append(plan) or FakeProcess(),
    )

    assert started == [outcome.plan]
    assert outcome.plan.source == source
    assert repository.get(result.job.source, result.job.id)["status"] == "opened"
