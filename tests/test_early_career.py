from __future__ import annotations

from ai_job_agent.models import JobPosting
from ai_job_agent.services.early_career import (
    filter_early_career_jobs,
    has_early_career_signal,
    is_early_career_job,
)


def job(title: str, **values: object) -> JobPosting:
    return JobPosting(
        title=title,
        company="Example",
        job_url=str(values.pop("job_url", "https://careers.example.com/jobs/1")),
        **values,
    )


def test_detects_chinese_and_english_early_career_signals() -> None:
    assert has_early_career_signal(job("2027届校园招聘-数据分析师"))
    assert has_early_career_signal(job("Software Engineer", job_type="Internship"))
    assert has_early_career_signal(job("Graduate AI Engineer"))


def test_normal_board_requires_positive_early_career_evidence() -> None:
    assert is_early_career_job(job("Data Analyst")) is False
    assert is_early_career_job(job("Data Analyst Intern")) is True


def test_verified_campus_channel_keeps_plain_titles_but_not_senior_roles() -> None:
    assert is_early_career_job(
        job("软件开发工程师"), verified_campus_channel=True
    ) is True
    assert is_early_career_job(
        job("Senior Software Engineer"), verified_campus_channel=True
    ) is False
    assert is_early_career_job(
        job("算法工程师", requirements="需要 5 年以上相关经验"),
        verified_campus_channel=True,
    ) is False


def test_positive_trainee_word_wins_over_generic_manager_word() -> None:
    assert is_early_career_job(job("Management Trainee")) is True


def test_filter_preserves_input_order() -> None:
    roles = [job("Senior Engineer"), job("Data Intern"), job("Graduate Engineer")]
    assert [item.title for item in filter_early_career_jobs(roles)] == [
        "Data Intern",
        "Graduate Engineer",
    ]
