from types import SimpleNamespace

from ai_job_agent.models import CandidateProfile, Education, Experience, JobPosting, Project
from ai_job_agent.services.answer_generator import AnswerGenerator, generate_answer


def _profile():
    return CandidateProfile(
        name="测试候选人",
        school="示例大学",
        degree="本科",
        major="数据科学",
        skills=["Python", "SQL", "Excel"],
        internships=[Experience(company="示例科技", title="数据分析实习生")],
        projects=[Project(name="销售分析看板", description="使用 SQL 清洗数据", technologies=["SQL"])],
        target_roles=["数据分析师"],
    )


def _job():
    return JobPosting(
        title="商业智能实习生",
        company="示例公司",
        description="使用 Python、SQL 分析业务数据",
        required_skills=["Python", "SQL"],
    )


def test_unknown_salary_and_work_authorization_require_user_confirmation():
    salary = generate_answer("你的期望薪资是多少？", _profile(), _job(), use_openai=False)
    permit = generate_answer("Are you authorized to work here?", _profile(), _job(), use_openai=False)

    assert salary.needs_user_confirmation and salary.answer is None
    assert salary.missing_fields == ["salary_preference"]
    assert permit.needs_user_confirmation and permit.answer is None
    assert permit.missing_fields == ["work_authorization"]


def test_known_salary_can_be_used_verbatim():
    profile = _profile().model_copy(update={"expected_salary": "面议"})
    result = generate_answer("期望薪资？", profile, use_openai=False)
    assert result.answer == "面议"
    assert result.needs_user_confirmation is False


def test_open_question_uses_existing_facts_and_honors_character_limit():
    result = generate_answer(
        "为什么申请这个岗位？", _profile(), _job(), char_limit=120, use_openai=False
    )
    assert result.needs_user_confirmation is False
    assert result.answer is not None and len(result.answer) <= 120
    assert "示例公司" in result.answer
    assert "商业智能实习生" in result.answer
    assert "Python" in result.answer


def test_personal_story_not_in_resume_is_never_invented():
    result = generate_answer("请介绍你最大的失败", _profile(), use_openai=False)
    assert result.needs_user_confirmation is True
    assert result.missing_fields == ["personal_example"]


def test_llm_rewrite_with_new_numeric_claim_is_rejected():
    class Responses:
        def parse(self, **kwargs):
            return SimpleNamespace(output_parsed={"answer": "我有 10 年数据分析经验。"})

    generator = AnswerGenerator(use_openai=True, client=SimpleNamespace(responses=Responses()))
    result = generator.generate("为什么申请这个岗位？", _profile(), _job())
    assert result.source == "local"
    assert "10 年" not in (result.answer or "")
