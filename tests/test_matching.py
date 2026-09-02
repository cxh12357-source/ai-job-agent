from job_assistant.matching import rank_jobs, score_job
from job_assistant.models import Criteria, Job


def make_job(**changes):
    values = {
        "id": "1",
        "title": "AI Agent Python Engineer",
        "company": "Acme",
        "location": "上海 / Remote",
        "description": "Build LLM and RAG products with Python, SQL and REST API.",
        "url": "https://example.com/1",
        "source": "test",
    }
    values.update(changes)
    return Job(**values)


def test_matching_job_is_eligible_and_explainable():
    criteria = Criteria.from_dict(
        {
            "target_titles": ["AI Agent"],
            "locations": ["上海"],
            "required_keywords": ["Python"],
            "preferred_keywords": ["LLM", "RAG", "SQL"],
            "excluded_keywords": ["Senior"],
            "minimum_score": 70,
        }
    )
    result = score_job(make_job(), "Python SQL LLM RAG API", criteria)

    assert result.eligible is True
    assert result.score >= 90
    assert "Python" in result.matched_keywords
    assert any("简历与岗位技能重合" in reason for reason in result.reasons)


def test_excluded_keyword_blocks_job_even_with_high_score():
    criteria = Criteria.from_dict(
        {
            "target_titles": ["AI"],
            "locations": ["上海"],
            "required_keywords": ["Python"],
            "excluded_keywords": ["Senior"],
            "minimum_score": 0,
        }
    )
    result = score_job(make_job(title="Senior AI Engineer"), "Python", criteria)

    assert result.eligible is False
    assert result.excluded_hits == ("Senior",)


def test_title_level_exclusion_does_not_block_intern_mentioning_senior_teammates():
    criteria = Criteria.from_dict(
        {
            "target_titles": ["BI Intern"],
            "locations": ["北京"],
            "required_keywords": ["Python"],
            "excluded_keywords": ["Senior", "10+ years"],
            "minimum_score": 0,
        }
    )
    result = score_job(
        make_job(
            title="BI Intern",
            location="北京",
            description=(
                "Use Python and Excel while learning from senior BI analysts. "
                "No prior experience required."
            ),
        ),
        "Python Excel",
        criteria,
    )

    assert result.eligible is True
    assert result.excluded_hits == ()


def test_rank_puts_eligible_jobs_first_and_limits_results():
    criteria = Criteria.from_dict(
        {
            "target_titles": ["Python"],
            "locations": ["上海"],
            "required_keywords": ["Python"],
            "minimum_score": 0,
            "max_results": 1,
        }
    )
    jobs = [
        make_job(id="bad", title="Designer", location="北京"),
        make_job(id="good", title="Python Engineer"),
    ]

    ranked = rank_jobs(jobs, "Python", criteria)
    assert len(ranked) == 1
    assert ranked[0].job.id == "good"


def test_latin_skill_terms_use_boundaries_instead_of_substrings():
    criteria = Criteria.from_dict(
        {
            "target_titles": ["AI"],
            "required_keywords": ["Java", "Go"],
            "minimum_score": 0,
        }
    )
    result = score_job(
        make_job(
            title="Paid Media Specialist",
            description="Work with JavaScript and Django applications.",
        ),
        "Java Go",
        criteria,
    )

    assert result.eligible is False
    assert result.matched_keywords == ()
    assert result.missing_required == ("Java", "Go")


def test_common_aliases_are_explainable_without_changing_user_term():
    criteria = Criteria.from_dict(
        {
            "target_titles": ["人工智能"],
            "locations": ["远程"],
            "required_keywords": ["大模型"],
            "minimum_score": 0,
        }
    )
    result = score_job(
        make_job(
            title="AI Engineer",
            location="Remote",
            description="Build large language model applications.",
        ),
        "LLM",
        criteria,
    )

    assert result.eligible is True
    assert result.matched_keywords == ("大模型",)


def test_bilingual_job_title_and_city_aliases_work_in_full_matcher():
    criteria = Criteria.from_dict(
        {
            "target_titles": ["数据分析实习生"],
            "locations": ["北京"],
            "preferred_keywords": ["Excel"],
            "minimum_score": 60,
        }
    )
    result = score_job(
        make_job(
            title="BI Intern",
            location="Beijing, China",
            description="Build reports in Excel and learn SQL.",
        ),
        "Python Excel 数据分析",
        criteria,
    )

    assert result.eligible is True
    assert any("岗位名称命中：数据分析实习生" in reason for reason in result.reasons)
    assert any("地点命中：北京" in reason for reason in result.reasons)
