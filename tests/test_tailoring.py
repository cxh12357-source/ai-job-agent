from collections import Counter

import pytest

from job_assistant.tailoring import TailoringError, tailor_resume


def test_missing_jd_skills_are_reported_but_never_injected_into_resume():
    resume = """陈同学
技能
- Python
- Excel
项目经历
- 使用 Python 清洗销售数据并生成 Excel 报告"""
    jd = "We need Python, SQL and Tableau for data analysis."

    result = tailor_resume(resume, jd, job_title="BI Intern", company="Example")

    assert result.covered_keywords == ("Python",)
    assert result.missing_keywords == ("SQL", "Tableau", "Data Analysis")
    assert "SQL" not in result.tailored_text
    assert "Tableau" not in result.tailored_text
    assert any("只有真实具备" in gap for gap in result.gaps)


def test_reorders_existing_sections_and_bullets_by_jd_relevance():
    resume = """陈同学
教育经历
某大学 本科
工作经历
• 协助整理部门文档
• 使用 Python 和 Excel 完成数据分析
技能
• Python
• SQL
• 沟通"""
    jd = "The intern will use Python, SQL, Excel and data analysis."

    result = tailor_resume(resume, jd)

    assert result.tailored_text.index("技能") < result.tailored_text.index("教育经历")
    assert result.tailored_text.index("- 使用 Python") < result.tailored_text.index("- 协助整理")
    assert any(change.kind == "reorder_sections" for change in result.changes)
    assert any(change.kind == "reorder_bullets" for change in result.changes)
    assert "•" not in result.tailored_text


def test_recognizes_internship_heading_and_data_analysis_evidence_aliases():
    resume = """Candidate Name
EDUCATION
B.Eng. Candidate
INTERNSHIP EXPERIENCE
- Used Python for data cleaning and statistical analysis
SKILLS
- Python"""

    result = tailor_resume(resume, "Python and data analysis are required.")

    assert "Data Analysis" in result.covered_keywords
    assert result.tailored_text.index("INTERNSHIP EXPERIENCE") < result.tailored_text.index("EDUCATION")


def test_preserves_each_original_fact_payload():
    resume = """姓名：陈同学
实习经历
- 在制造团队分析异常数据
- 编写周报
教育经历
智能制造工程 本科"""

    result = tailor_resume(resume, "需要制造、数据分析和 Excel 能力")

    original_payloads = Counter(
        line.lstrip("-•·* ") for line in resume.splitlines() if line.strip()
    )
    tailored_payloads = Counter(
        line.lstrip("-•·* ") for line in result.tailored_text.splitlines() if line.strip()
    )
    assert tailored_payloads == original_payloads
    assert "Excel" not in result.tailored_text
    assert "Excel" in result.missing_keywords


def test_flags_experience_degree_and_work_authorization_for_manual_review():
    result = tailor_resume(
        "张同学\n教育经历\n某大学 本科\n技能\n- Python",
        "Requires a Master's degree, 3+ years of experience and work authorization. Python required.",
    )

    joined = " ".join(result.warnings)
    assert "3 年" in joined
    assert "硕士" in joined
    assert "工作许可" in joined


def test_result_can_be_serialized_and_previewed_as_markdown():
    result = tailor_resume(
        "李同学\n技能\n- Python",
        "Python and SQL are required.",
        job_title="Data Intern",
        company="Acme",
    )

    payload = result.to_dict()
    preview = result.to_markdown()

    assert payload["job_title"] == "Data Intern"
    assert payload["missing_keywords"] == ["SQL"]
    assert payload["coverage_ratio"] == 0.5
    assert "# 简历定制草稿：Acme / Data Intern" in preview
    assert "缺口提示（不要在没有事实依据时写入简历）" in preview
    assert result.tailored_text in preview


@pytest.mark.parametrize(
    ("resume", "jd", "message"),
    [("", "Python", "简历正文"), ("有内容", "", "岗位 JD")],
)
def test_rejects_empty_inputs(resume, jd, message):
    with pytest.raises(TailoringError, match=message):
        tailor_resume(resume, jd)
