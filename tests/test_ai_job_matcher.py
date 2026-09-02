from types import SimpleNamespace

from ai_job_agent.models import CandidateProfile, Education, Experience, JobPosting, Project
from ai_job_agent.services.job_matcher import JobMatcher, WEIGHTS, match_job


def _matching_profile():
    return CandidateProfile(
        location="上海",
        target_locations=["上海"],
        target_roles=["AI Data Engineer"],
        skills=["Python", "SQL", "Machine Learning"],
        education=[Education(school="Example University", degree="Bachelor", major="Computer Science")],
        internships=[Experience(company="Example", title="AI Data Engineer", description="machine learning data engineering")],
        projects=[Project(name="ML Pipeline", technologies=["Python", "SQL", "Machine Learning"])],
    )


def _job():
    return JobPosting(
        title="AI Data Engineer",
        company="Example",
        location="Shanghai",
        description="Build machine learning data products with Python and SQL.",
        requirements="Bachelor in Computer Science.",
        required_skills=["Python", "SQL", "Machine Learning"],
        job_url="https://example.test/job",
    )


def test_hybrid_matcher_uses_declared_weights_and_explains_score():
    result = match_job(_matching_profile(), _job(), use_openai=False)

    assert result.weights == WEIGHTS
    assert sum(result.score_breakdown.values()) == result.base_score
    assert result.match_score == 100
    assert result.match_level == "强烈推荐"
    assert result.matched_skills == ["Machine Learning", "Python", "SQL"]
    assert result.missing_skills == []


def test_mismatch_is_not_hidden_by_llm_style_guessing():
    profile = CandidateProfile(
        location="北京",
        target_locations=["北京"],
        target_roles=["Finance Analyst"],
        skills=["Excel"],
        education=[Education(degree="Bachelor", major="Finance")],
    )
    result = match_job(profile, _job(), use_openai=False)
    assert result.match_score < 60
    assert result.match_level == "默认隐藏"
    assert {"Python", "SQL", "Machine Learning"} <= set(result.missing_skills)


def test_optional_llm_review_is_bounded_to_five_points():
    class Responses:
        def parse(self, **kwargs):
            return SimpleNamespace(output_parsed={"adjustment": -5})

    client = SimpleNamespace(responses=Responses())
    deterministic = JobMatcher(use_openai=False).match(_matching_profile(), _job())
    reviewed = JobMatcher(use_openai=True, client=client).match(_matching_profile(), _job())
    assert reviewed.base_score == deterministic.base_score
    assert reviewed.llm_adjustment == -5
    assert reviewed.match_score == deterministic.match_score - 5


def test_fresh_graduate_profile_gets_truthful_campus_fit_explanation():
    profile = _matching_profile().model_copy(update={"career_stage": "fresh_graduate"})
    campus = _job().model_copy(
        update={
            "title": "2027届 Graduate AI Engineer",
            "description": "面向应届毕业生，使用 Python 构建 AI 应用。",
            "job_url": "https://careers.example.com/campus/jobs/1",
        }
    )
    senior = _job().model_copy(
        update={
            "title": "Senior AI Engineer",
            "description": "Requires 5+ years of experience.",
            "job_url": "https://careers.example.com/jobs/2",
        }
    )

    campus_result = match_job(profile, campus, use_openai=False)
    senior_result = match_job(profile, senior, use_openai=False)

    assert any("应届生" in item for item in campus_result.advantages)
    assert any("资深" in item for item in senior_result.risks)
