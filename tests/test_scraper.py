from __future__ import annotations

import json
from pathlib import Path

import pytest

import scraper
from job_assistant.matching import rank_jobs
from job_assistant.models import Criteria
from job_assistant.storage import ApplicationRepository


def _boss_text(*, url: str = "https://www.zhipin.com/job_detail/abc123.html") -> str:
    return f"""岗位名称：热管理工程师（校招）
公司名称：测试汽车
工作地点：上海
薪资范围：15-25K/月
岗位链接：{url}
职位描述：
负责热管理系统测试、数据分析和工程验证。
要求掌握 Python、Excel，并能阅读机械图纸。
"""


def test_build_boss_search_url_encodes_query_and_shanghai_city():
    url = scraper.build_official_search_url("BOSS直聘", "热管理 工程师", city="上海")

    assert url == (
        "https://www.zhipin.com/web/geek/job?"
        "query=%E7%83%AD%E7%AE%A1%E7%90%86+%E5%B7%A5%E7%A8%8B%E5%B8%88&city=101020100"
    )


@pytest.mark.parametrize(
    ("platform", "keyword", "city"),
    [
        ("未知平台", "工程师", "上海"),
        ("boss", "", "上海"),
        ("boss", "工程师", "上海&city=evil"),
    ],
)
def test_search_url_rejects_unknown_platform_blank_query_and_city_injection(
    platform: str, keyword: str, city: str
):
    with pytest.raises(scraper.JobImportError):
        scraper.build_official_search_url(platform, keyword, city=city)


@pytest.mark.parametrize("platform", ["boss", "猎聘"])
def test_live_boss_and_liepin_are_policy_blocked_before_browser_launch(platform: str):
    with pytest.raises(scraper.RestrictedPlatformError, match="不会启动自动抓取"):
        scraper.live_scrape(platform, keyword="工程师")


def test_pasted_boss_job_maps_all_fields_and_salary():
    jobs = scraper.parse_pasted_jobs(_boss_text(), "boss")

    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "热管理工程师(校招)"
    assert job.company == "测试汽车"
    assert job.location == "上海"
    assert job.salary_text == "15-25K/月"
    assert job.salary_min == 15_000
    assert job.salary_max == 25_000
    assert job.currency == "CNY"
    assert job.period == "month"
    assert "工程验证" in job.description
    assert job.url == "https://www.zhipin.com/job_detail/abc123.html"
    assert job.source == "UserProvided:BOSS直聘"


def test_location_is_required():
    text = _boss_text().replace("工作地点：上海\n", "")

    with pytest.raises(scraper.JobImportError, match="工作地点不能为空"):
        scraper.parse_pasted_jobs(text, "boss")


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://www.zhipin.com/job_detail/abc.html", "HTTPS"),
        ("https://evil.example/job_detail/abc.html", "官方域名"),
        ("https://www.zhipin.com/web/geek/job?query=python", "单个岗位详情页"),
        ("https://name:secret@www.zhipin.com/job_detail/abc.html", "账号、口令"),
        (
            "https://www.zhipin.com/job_detail/请替换为真实岗位ID.html",
            "路径格式无效",
        ),
    ],
)
def test_job_url_must_be_a_safe_official_detail_url(url: str, message: str):
    with pytest.raises(scraper.JobImportError, match=message):
        scraper.parse_pasted_jobs(_boss_text(url=url), "boss")


def test_tracking_query_and_fragment_are_not_persisted():
    jobs = scraper.parse_pasted_jobs(
        _boss_text(
            url="https://www.zhipin.com/job_detail/abc123.html?ka=search_list_1#tracking"
        ),
        "boss",
    )

    assert jobs[0].url == "https://www.zhipin.com/job_detail/abc123.html"


def test_multiple_blocks_are_parsed_and_duplicate_urls_are_removed():
    combined = _boss_text() + "\n===JOB===\n" + _boss_text()

    jobs = scraper.parse_pasted_jobs(combined, "boss")

    assert len(jobs) == 1


def test_json_import_ignores_caller_source_and_uses_safe_source():
    payload = {
        "jobs": [
            {
                "title": "数据分析实习生",
                "company": "测试科技",
                "location": "北京",
                "salary": "200-300元/天",
                "url": "https://www.liepin.com/job/123456.shtml?from=tracker",
                "description": "分析业务数据，要求掌握 Python 和 SQL。",
                "source": "untrusted-source",
            }
        ]
    }

    jobs = scraper.parse_json_jobs(json.dumps(payload, ensure_ascii=False), "猎聘")

    assert jobs[0].source == "UserProvided:猎聘"
    assert jobs[0].url == "https://www.liepin.com/job/123456.shtml"
    assert jobs[0].salary_text == "200-300元/天"


def test_saved_html_reads_complete_jobposting_jsonld():
    html = """
    <html><head><script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "JobPosting",
      "title": "机械设计工程师",
      "hiringOrganization": {"@type": "Organization", "name": "测试制造"},
      "jobLocation": {"address": {
        "addressLocality": "苏州", "addressRegion": "江苏", "addressCountry": "中国"
      }},
      "description": "<p>负责结构设计。</p><p>要求掌握 SolidWorks。</p>",
      "url": "https://www.liepin.com/job/987654.shtml",
      "baseSalary": {"currency": "CNY", "value": {
        "minValue": 12000, "maxValue": 18000, "unitText": "MONTH"
      }}
    }
    </script></head></html>
    """

    jobs = scraper.parse_saved_html(html, "liepin")

    assert jobs[0].company == "测试制造"
    assert jobs[0].location == "苏州 江苏 中国"
    assert jobs[0].description == "负责结构设计。\n要求掌握 SolidWorks。"
    assert jobs[0].salary_min == 12000
    assert jobs[0].salary_max == 18000
    assert jobs[0].period == "month"


def test_saved_html_without_complete_jobposting_is_rejected():
    with pytest.raises(scraper.JobImportError, match="没有完整 JobPosting"):
        scraper.parse_saved_html("<html><title>普通网页</title></html>", "boss")


def test_imported_job_round_trips_through_matching_and_database(tmp_path: Path):
    job = scraper.parse_pasted_jobs(_boss_text(), "boss")[0]
    criteria = Criteria.from_dict(
        {
            "target_titles": ["热管理工程师"],
            "locations": ["上海"],
            "preferred_keywords": ["Python", "测试"],
            "minimum_score": 0,
        }
    )
    results = rank_jobs([job], "掌握 Python 和热管理系统测试", criteria)
    repository = ApplicationRepository(tmp_path / "applications.db")

    repository.save_results(results)
    stored = repository.get(job.source, job.id)

    assert stored is not None
    assert stored["location"] == "上海"
    assert stored["description"] == job.description
    assert stored["salary_text"] == "15-25K/月"
    assert stored["url"] == job.url


def test_import_size_is_bounded():
    oversized = b"x" * (scraper.MAX_IMPORT_BYTES + 1)

    with pytest.raises(scraper.JobImportError, match="不能超过"):
        scraper.parse_imported_file("jobs.txt", oversized, "boss")


def test_scraper_has_no_stealth_challenge_or_form_submission_capability():
    source = Path(scraper.__file__).read_text(encoding="utf-8")
    forbidden = (
        "AutomationControlled",
        "navigator.webdriver",
        "add_init_script",
        "drag_to",
        "set_input_files",
        ".fill(",
        ".check(",
        ".submit(",
    )

    assert not [token for token in forbidden if token in source]
