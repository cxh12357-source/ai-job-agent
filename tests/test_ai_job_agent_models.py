import pytest

from ai_job_agent.models import (
    ApplicationReview,
    CandidateProfile,
    Education,
    FieldSchema,
    JobPosting,
    MatchAssessment,
)


def test_candidate_profile_keeps_unknowns_nullable_and_syncs_primary_education():
    profile = CandidateProfile(
        name="Jordan Example",
        education=[Education(school="Example University", degree="Bachelor")],
    )

    assert profile.school == "Example University"
    assert profile.degree == "Bachelor"
    assert profile.expected_salary is None
    assert profile.internships is None


def test_job_posting_accepts_url_compatibility_alias():
    job = JobPosting(title="Data Intern", company="Example", url="https://example.test/job")
    assert job.job_url == "https://example.test/job"
    assert job.url == job.job_url


@pytest.mark.parametrize(
    ("score", "level"),
    [(95, "强烈推荐"), (85, "推荐"), (75, "可以投"), (65, "谨慎"), (59, "默认隐藏")],
)
def test_match_level_thresholds(score, level):
    assert MatchAssessment(match_score=score).match_level == level


def test_field_and_application_review_enforce_human_gates():
    uncertain = FieldSchema(label="是否需要签证", confidence=0.4)
    assert uncertain.needs_user_confirmation is True

    blocked = ApplicationReview(
        company="Example",
        job_title="Intern",
        checks={"name": True, "expected_salary": False},
        needs_user_confirmation=["expected_salary"],
    )
    assert blocked.required_field_missing is True
    assert blocked.ready_to_submit is False
    assert blocked.status == "waiting_user"

    ready = ApplicationReview(
        company="Example", job_title="Intern", checks={"name": True, "email": True}
    )
    assert ready.ready_to_submit is True
    assert ready.status == "ready_to_submit"
