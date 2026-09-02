from types import SimpleNamespace

from ai_job_agent.models import CandidateProfile
from ai_job_agent.services.profile_builder import ProfileBuilder, build_candidate_profile


RESUME = """
测试候选人
手机：138 0013 8000
邮箱：candidate@example.com
所在地：上海
学校：示例大学
学历：本科
专业：数据科学
毕业时间：2027-06
技能：Python、SQL、Excel、数据分析
目标岗位：数据分析实习生
目标地点：上海
实习经历：示例科技 - 数据分析实习生 - 使用 Python 和 SQL 清洗数据
项目：销售分析看板 - 使用 Power BI 展示指标
"""


# The names and organisations below are fictional, while the layout mirrors a
# common one-page Chinese campus-recruiting resume extracted from PDF/DOCX.
SECTIONED_CHINESE_RESUME = """
林若川 | 手机：138 2468 1357 | 邮箱：ruochuan.lin@example.com | 现居地：杭州
求职意向：数据分析实习生

教育背景
2022.09 - 2026.06 南江理工大学 数据科学与大数据技术 本科 预计毕业：2026年6月

实习经历
星河数据科技有限公司 | 数据分析实习生 | 上海 | 2025.06 - 2025.09
• 使用 Python 与 SQL 清洗业务数据，形成周报
• 搭建 Power BI 看板，跟踪核心指标
云杉零售有限公司 | 商业分析实习生 | 杭州 | 2024.07 - 2024.09
• 整理门店销售数据并用 Excel 完成复盘

工作经历
远帆公益发展中心 | 数据运营助理 | 杭州 | 2023.10 - 2024.01
• 维护活动数据台账，按月核对报名口径
青禾文化工作室 | 内容运营助理 | 远程 | 2023.03 - 2023.06
• 归档活动素材并整理发布记录

项目经历
校园客流预测 | 项目负责人 | 2025.02 - 2025.05
• 使用 Pandas 完成特征整理
• 训练并评估时间序列基线模型
商品评价分析 | 课程项目 | 2024.09 - 2024.12
• 用 Python 整理匿名评价数据并输出可视化报告

专业技能
Python、SQL、Excel、Power BI、Pandas

语言能力
英语：CET-6，可用于工作沟通

个人概述
注重数据口径与结果复核，能够独立完成从数据清洗到可视化呈现的分析流程。
希望在真实业务场景中持续提升分析与沟通能力。
"""


def test_local_profile_builder_extracts_only_explicit_resume_facts():
    profile = build_candidate_profile(RESUME, use_openai=False)

    assert profile.name == "测试候选人"
    assert profile.phone == "13800138000"
    assert profile.email == "candidate@example.com"
    assert profile.location == "上海"
    assert profile.school == "示例大学"
    assert profile.degree == "本科"
    assert profile.major == "数据科学"
    assert {"Python", "SQL", "Excel", "Data Analysis"} <= set(profile.skills or [])
    assert profile.internships and profile.internships[0].company == "示例科技"
    assert profile.internships[0].description == "使用 Python 和 SQL 清洗数据"
    assert profile.projects and profile.projects[0].name == "销售分析看板"
    assert profile.projects[0].description == "使用 Power BI 展示指标"
    assert profile.expected_salary is None
    assert profile.parse_method == "local"


def test_local_profile_builder_parses_sectioned_chinese_resume_without_inventing():
    profile = build_candidate_profile(SECTIONED_CHINESE_RESUME, use_openai=False)

    assert profile.name == "林若川"
    assert profile.phone == "13824681357"
    assert profile.email == "ruochuan.lin@example.com"
    assert profile.location == "杭州"

    assert profile.school == "南江理工大学"
    assert profile.degree == "本科"
    assert profile.major == "数据科学与大数据技术"
    assert profile.graduation_date == "2026年6月"
    assert profile.education and profile.education[0].school == "南江理工大学"

    assert profile.internships and len(profile.internships) == 2
    assert profile.internships[0].company == "星河数据科技有限公司"
    assert profile.internships[0].title == "数据分析实习生"
    assert profile.internships[0].start_date == "2025.06"
    assert profile.internships[0].end_date == "2025.09"
    assert "形成周报" in (profile.internships[0].description or "")
    assert "Power BI 看板" in (profile.internships[0].description or "")
    assert profile.internships[1].company == "云杉零售有限公司"

    assert profile.work_experience and len(profile.work_experience) == 2
    assert profile.work_experience[0].company == "远帆公益发展中心"
    assert profile.work_experience[1].title == "内容运营助理"
    assert profile.work_experience[1].location == "远程"

    assert profile.projects and len(profile.projects) == 2
    assert profile.projects[0].name == "校园客流预测"
    assert profile.projects[0].role == "项目负责人"
    assert "Pandas" in (profile.projects[0].description or "")
    assert profile.projects[1].name == "商品评价分析"

    assert {"Python", "SQL", "Excel", "Power BI", "Pandas"} <= set(profile.skills or [])
    assert profile.languages and profile.languages[0].name == "英语"
    assert profile.languages[0].proficiency == "CET-6"
    assert profile.self_introduction == (
        "注重数据口径与结果复核，能够独立完成从数据清洗到可视化呈现的分析流程。\n"
        "希望在真实业务场景中持续提升分析与沟通能力。"
    )

    structured_values = [
        *(item.company for item in profile.internships),
        *(item.company for item in profile.work_experience),
        *(item.name for item in profile.projects),
    ]
    assert not {"实习经历", "工作经历", "项目经历"} & set(structured_values)
    assert profile.expected_salary is None


def test_local_profile_builder_keeps_english_section_format_compatible():
    resume = """
Alex Chen | Phone: 13800138000 | Email: alex.chen@example.com
Education
Example University | Computer Science | Bachelor | 2022.09 - 2026.06
Internship Experience
Northstar Analytics Ltd. | Data Analyst Intern | Remote | 2025.06 - 2025.08
- Built SQL reports from explicitly supplied source data
Professional Summary
Careful analyst who validates source data before reporting results.
"""

    profile = build_candidate_profile(resume, use_openai=False)

    assert profile.name == "Alex Chen"
    assert profile.school == "Example University"
    assert profile.degree == "Bachelor"
    assert profile.major == "Computer Science"
    assert profile.graduation_date == "2026.06"
    assert profile.internships and profile.internships[0].company == "Northstar Analytics Ltd."
    assert profile.internships[0].title == "Data Analyst Intern"
    assert profile.internships[0].location == "Remote"
    assert profile.internships[0].description == "Built SQL reports from explicitly supplied source data"
    assert profile.self_introduction == (
        "Careful analyst who validates source data before reporting results."
    )


def test_local_profile_builder_handles_flattened_chinese_docx_header_and_rows():
    resume = """
赵安宁 智能制造工程本科生 | 工程数据分析 138 2468 1357 | zhao.anning@example.com
教育背景
东江理工大学 本科 · 智能制造工程 相关课程：工程热力学、自动控制原理
2023.09 - 2027.06
实习经历
星河汽车有限责任公司 热管理工程师（实习）
2025.06 - 2025.09
• 使用 Python 整理测试数据并复核异常记录。
云杉科技有限公司 数据分析助理（实习）
2024.06 - 2024.09
• 使用 Excel 完成业务数据清洗。
"""

    profile = build_candidate_profile(resume, use_openai=False)

    assert profile.name == "赵安宁"
    assert profile.major == "智能制造工程"
    assert profile.graduation_date == "2027.06"
    assert profile.internships and len(profile.internships) == 2
    assert profile.internships[0].company == "星河汽车有限责任公司"
    assert profile.internships[0].title == "热管理工程师（实习）"
    assert profile.internships[1].company == "云杉科技有限公司"
    assert profile.internships[1].start_date == "2024.06"


class _Responses:
    def __init__(self, parsed):
        self.parsed = parsed
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_parsed=self.parsed)


class _Client:
    def __init__(self, parsed):
        self.responses = _Responses(parsed)


def test_openai_profile_is_sanitized_against_original_resume():
    parsed = CandidateProfile(
        name="测试候选人",
        email="candidate@example.com",
        skills=["Python", "Rust"],
        career_stage="fresh_graduate",
        expected_salary="50000 元/月",
    )
    client = _Client(parsed)

    profile = ProfileBuilder(use_openai=True, client=client).build(RESUME)

    assert profile.name == "测试候选人"
    assert profile.skills == ["Python"]
    assert profile.expected_salary is None
    assert profile.career_stage is None
    assert profile.parse_method == "openai"
    assert client.responses.kwargs["store"] is False
    assert client.responses.kwargs["text_format"] is CandidateProfile


def test_openai_failure_falls_back_without_crashing():
    class BrokenResponses:
        def parse(self, **kwargs):
            raise RuntimeError("network unavailable")

    client = SimpleNamespace(responses=BrokenResponses())
    profile = ProfileBuilder(use_openai=True, client=client).build(RESUME)
    assert profile.parse_method == "local"
    assert profile.email == "candidate@example.com"
    assert any("回退" in warning for warning in profile.parse_warnings)
