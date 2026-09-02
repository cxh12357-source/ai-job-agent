from job_assistant.candidate_signals import extract_candidate_signals


def test_extracts_only_resume_evidenced_engineering_data_and_ai_signals():
    resume = """智能制造工程本科
新能源汽车热管理实习，使用 Python 和 Excel 做数据分析及可视化。
掌握 AutoCAD、SolidWorks，并用 AI Agent 和 LLM 优化工作流。"""

    result = extract_candidate_signals(resume)

    assert {"Python", "Excel", "Data Analysis", "AI Agent", "LLM"} <= set(result.keywords)
    assert {"Thermal Management", "Manufacturing", "SolidWorks", "AutoCAD"} <= set(result.keywords)
    assert "热管理工程师" in result.suggested_titles
    assert "制造工程师" in result.suggested_titles
    assert "数据分析实习生" in result.suggested_titles
    assert "AI应用" in result.suggested_titles


def test_does_not_invent_unmentioned_skills_or_titles():
    result = extract_candidate_signals("英语沟通与客户销售")

    assert "Python" not in result.keywords
    assert "Thermal Management" not in result.keywords
    assert "热管理工程师" not in result.suggested_titles


def test_empty_resume_has_no_automatic_assumptions():
    assert extract_candidate_signals("").keywords == ()
    assert extract_candidate_signals("").suggested_titles == ()
