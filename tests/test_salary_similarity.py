from __future__ import annotations

import pytest

from job_assistant.matching import evaluate_salary, score_job
from job_assistant.models import Criteria, Job
from job_assistant.similarity import find_similar_jobs, score_similarity


def make_job(**changes) -> Job:
    values = {
        "id": "reference",
        "title": "AI Engineer",
        "company": "Acme",
        "location": "上海",
        "description": "Build Python LLM and RAG products.",
        "url": "https://example.com/jobs/reference",
        "source": "test",
        "department": "AI Platform",
    }
    values.update(changes)
    return Job(**values)


def test_job_and_criteria_support_salary_and_language_fields():
    job = make_job(
        salary_min=20,
        salary_max=30,
        currency="CNY",
        period="月",
        salary_text="20-30K·14薪",
        language="en",
    )
    criteria = Criteria.from_dict(
        {
            # 简写键也可以从 UI/JSON 配置中安全转换。
            "salary_min": "20",
            "salary_max": "35",
            "currency": "cny",
            "period": "月",
            "salary_unknown_policy": "review",
        }
    )

    assert job.salary_text == "20-30K·14薪"
    assert job.language == "en"
    assert criteria.expected_salary_min == 20
    assert criteria.expected_salary_max == 35
    assert criteria.salary_currency == "CNY"
    assert criteria.unknown_salary_policy == "review"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            {"expected_salary_min": 30, "expected_salary_max": 20},
            "expected_salary_min",
        ),
        ({"expected_salary_min": "20k"}, "必须是数字"),
        ({"unknown_salary_policy": "guess"}, "include、review 或 exclude"),
    ],
)
def test_invalid_salary_criteria_are_rejected(data, message):
    with pytest.raises(ValueError, match=message):
        Criteria.from_dict(data)


def test_salary_overlap_is_an_explainable_eligibility_gate():
    criteria = Criteria.from_dict(
        {
            "expected_salary_min": 20,
            "expected_salary_max": 30,
            "salary_currency": "CNY",
            "salary_period": "month",
            "minimum_score": 0,
        }
    )

    matching = score_job(
        make_job(salary_min=18, salary_max=25, currency="RMB", period="月"),
        "Python LLM",
        criteria,
    )
    too_low = score_job(
        make_job(salary_min=10, salary_max=15, currency="CNY", period="monthly"),
        "Python LLM",
        criteria,
    )

    assert matching.salary_eligible is True
    assert matching.eligible is True
    assert "有重叠" in matching.salary_reason
    assert matching.salary_reason in matching.reasons
    assert too_low.salary_eligible is False
    assert too_low.eligible is False
    assert "低于期望" in too_low.salary_reason


def test_unknown_salary_policy_can_include_review_or_exclude():
    job = make_job(salary_text="Competitive")

    include = Criteria.from_dict(
        {"expected_salary_min": 20, "unknown_salary_policy": "include"}
    )
    review = Criteria.from_dict(
        {"expected_salary_min": 20, "unknown_salary_policy": "review"}
    )
    exclude = Criteria.from_dict(
        {"expected_salary_min": 20, "unknown_salary_policy": "exclude"}
    )

    assert evaluate_salary(job, include)[0] is True
    assert "保留未知薪资" in evaluate_salary(job, include)[1]
    assert evaluate_salary(job, review)[0] is True
    assert "人工核对" in evaluate_salary(job, review)[1]
    assert evaluate_salary(job, exclude)[0] is False
    assert "排除未知薪资" in evaluate_salary(job, exclude)[1]


def test_mismatched_currency_or_period_uses_unknown_salary_policy():
    job = make_job(salary_min=20, salary_max=30, currency="USD", period="year")
    criteria = Criteria.from_dict(
        {
            "expected_salary_min": 20,
            "salary_currency": "CNY",
            "salary_period": "month",
            "unknown_salary_policy": "exclude",
        }
    )

    eligible, reason = evaluate_salary(job, criteria)

    assert eligible is False
    assert "币种" in reason
    assert "安全比较" in reason


def test_incomplete_salary_range_is_not_silently_assumed():
    job = make_job(salary_min=10, salary_max=None, currency="CNY", period="month")
    criteria = Criteria.from_dict(
        {
            "expected_salary_min": 20,
            "salary_currency": "CNY",
            "salary_period": "month",
            "unknown_salary_policy": "exclude",
        }
    )

    eligible, reason = evaluate_salary(job, criteria)

    assert eligible is False
    assert "区间不完整" in reason


def test_similar_jobs_are_explainable_and_exclude_reference_itself():
    reference = make_job(language="en")
    same_role = make_job(
        id="same-role",
        title="Senior AI Engineer",
        company="Beta",
        url="https://example.com/jobs/same-role",
        language="en",
    )
    other_role = make_job(
        id="other-role",
        title="Product Designer",
        company="Gamma",
        description="User research and product design.",
        department="Design",
        url="https://example.com/jobs/other-role",
    )

    results = find_similar_jobs(reference, [other_role, reference, same_role])

    assert [item.job.id for item in results] == ["same-role"]
    assert results[0].score >= 80
    assert results[0].similarity_score == results[0].score
    assert results[0].shared_title_terms == ("ai",)
    assert any("共同技能" in reason for reason in results[0].reasons)


def test_similarity_does_not_match_ai_inside_paid():
    reference = make_job(
        title="AI Engineer",
        description="",
        department="",
        location="",
    )
    paid_media = make_job(
        id="paid",
        title="Paid Media Specialist",
        description="",
        department="",
        location="",
        url="https://example.com/jobs/paid",
    )

    result = score_similarity(reference, paid_media)

    assert result.score == 0
    assert result.shared_title_terms == ()
    assert find_similar_jobs(reference, [paid_media]) == []


def test_similar_job_order_is_stable_and_honors_top_n():
    reference = make_job()
    alpha = make_job(
        id="alpha",
        title="AI Engineer",
        company="Alpha",
        url="https://example.com/jobs/alpha",
    )
    beta = make_job(
        id="beta",
        title="AI Engineer",
        company="Beta",
        url="https://example.com/jobs/beta",
    )

    forward = find_similar_jobs(reference, [beta, alpha], limit=1)
    reverse = find_similar_jobs(reference, [alpha, beta], limit=1)

    assert [item.job.id for item in forward] == ["alpha"]
    assert [item.job.id for item in reverse] == ["alpha"]


def test_tracking_query_does_not_recommend_the_same_job():
    reference = make_job(url="https://example.com/jobs/42?utm_source=one")
    duplicate = make_job(
        id="different-id",
        url="https://example.com/jobs/42?utm_source=two",
    )

    assert find_similar_jobs(reference, [duplicate]) == []
