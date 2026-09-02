from __future__ import annotations

import pytest

from ai_job_agent.company_scope import (
    DOMESTIC_COMPANY_ALIASES,
    DOMESTIC_COMPANIES,
    DOMESTIC_OFFICIAL_DOMAINS,
    INTERNATIONAL_CAMPUS_COMPANIES,
    INTERNATIONAL_GREENHOUSE_TOKENS,
    classify_company,
    company_scope_for_job,
    filter_jobs_by_scope,
    scope_label,
)
from ai_job_agent.models import JobPosting
from ai_job_agent.sources.campus_catalog import campus_sites_for_scope
from job_assistant.discovery import DEFAULT_BOARD_CATALOG


EXPECTED_INTERNATIONAL_GREENHOUSE_TOKENS = frozenset(
    {
        "sesai",
        "guidepoint",
        "mongodb",
        "adyen",
        "databricks",
        "agoda",
        "impact",
        "forter",
        "xendit",
        "straitsx",
        "prophet",
        "maravailifesciences",
        "peakdesign",
        "quberesearchandtechnologies",
        "rzr",
        "worldquant",
        "fictiv",
        "sharkninjaoperatingllc",
        "mw-tech-grad",
        "thetradedesk",
        "ideo",
        "testendouble",
        "moloco",
        "thoughtworks",
    }
)
EXPECTED_UNKNOWN_GREENHOUSE_TOKENS = frozenset(
    {
        "casetify",
        "appier",
        "starchild",
        "okx",
        "eclipsetrading",
        "anthropic",
        "stripe",
        "cloudflare",
        "datadog",
        "twilio",
        "braze",
        "amplitude",
        "elastic",
        "gitlab",
        "grafanalabs",
        "canonical",
        "scaleai",
        "taboola",
        "cockroachlabs",
        "figma",
        "klaviyo",
        "remotecom",
    }
)


@pytest.mark.parametrize(
    "company",
    (
        "华为",
        "阿里巴巴",
        "联想",
        "海尔",
        "Li Auto",
        "ByteDance",
        "大疆创新",
        "比亚迪",
        "中兴通讯",
        "美的集团",
        "海康威视",
        "中国工商银行",
        "国家电网",
        "中国移动",
        "迈瑞医疗",
        "国药集团",
    ),
)
def test_known_domestic_companies_are_explicitly_classified(company: str) -> None:
    assert classify_company(company) == "domestic"


@pytest.mark.parametrize(
    ("company", "alias"),
    (
        ("大疆创新", "DJI"),
        ("比亚迪", "BYD Auto"),
        ("中兴通讯", "ZTE Corporation"),
        ("美的集团", "Midea Group"),
        ("TCL", "TCL Technology"),
        ("海康威视", "Hikvision"),
        ("长安汽车", "Changan Automobile"),
        ("上汽集团", "SAIC Motor"),
        ("广汽集团", "GAC Group"),
        ("商汤科技", "SenseTime"),
        ("寒武纪", "Cambricon"),
        ("小红书", "REDnote"),
        ("哔哩哔哩", "Bilibili"),
        ("科大讯飞", "iFlytek"),
        ("中国工商银行", "ICBC"),
        ("中国农业银行", "Agricultural Bank of China"),
        ("中国建设银行", "China Construction Bank"),
        ("招商银行", "China Merchants Bank"),
        ("交通银行", "Bank of Communications"),
        ("中国平安", "Ping An Group"),
        ("国家电网", "State Grid Corporation of China"),
        ("中国石油", "CNPC"),
        ("中国石化", "Sinopec"),
        ("南方电网", "China Southern Power Grid"),
        ("中国海油", "CNOOC"),
        ("国家能源集团", "CHN Energy"),
        ("中国华能", "China Huaneng"),
        ("中国移动", "China Mobile"),
        ("中国电信", "China Telecom"),
        ("中国联通", "China Unicom"),
        ("伊利集团", "Yili Group"),
        ("蒙牛乳业", "China Mengniu Dairy"),
        ("迈瑞医疗", "Mindray"),
        ("恒瑞医药", "Hengrui Pharma"),
        ("国药集团", "Sinopharm"),
    ),
)
def test_expanded_domestic_company_aliases_are_classified(
    company: str, alias: str
) -> None:
    assert company in DOMESTIC_COMPANIES
    assert DOMESTIC_COMPANY_ALIASES[alias] == company
    assert classify_company(alias) == "domestic"


def test_every_domestic_campus_company_is_in_the_reviewed_scope() -> None:
    domestic_sites = campus_sites_for_scope("domestic")

    assert len(domestic_sites) >= 50
    assert {site.company for site in domestic_sites} <= DOMESTIC_COMPANIES
    assert all(classify_company(site.company) == "domestic" for site in domestic_sites)


def test_reviewed_official_domains_classify_without_guessing_unknown_hosts() -> None:
    for domain in DOMESTIC_OFFICIAL_DOMAINS:
        assert (
            classify_company("Untrusted display name", source=f"Official website:{domain}")
            == "domestic"
        )

    # This shared ATS hostname serves both domestic and international employers.
    assert (
        classify_company("Untrusted display name", source="Official website:app.mokahr.com")
        == "unknown"
    )


@pytest.mark.parametrize(
    "company",
    ("西门子", "西门子医疗", "博世", "施耐德电气", "Siemens Healthineers"),
)
def test_known_international_companies_are_explicitly_classified(company: str) -> None:
    assert classify_company(company) == "international"


def test_greenhouse_source_token_classifies_without_guessing_api_company_name() -> None:
    job = JobPosting(
        title="Graduate Engineer",
        company="API-returned legal entity",
        job_url="https://job-boards.greenhouse.io/mongodb/jobs/1",
        source="Greenhouse:mongodb",
    )
    assert company_scope_for_job(job) == "international"
    for token in EXPECTED_UNKNOWN_GREENHOUSE_TOKENS:
        assert classify_company("Unknown", source=f"Greenhouse:{token}") == "unknown"
    assert classify_company("RZR Global Inc.", source="Greenhouse:rzr") == "international"


def test_every_current_greenhouse_catalog_token_has_an_explicit_expected_scope() -> None:
    catalog_tokens = frozenset(board.token for board in DEFAULT_BOARD_CATALOG)

    assert INTERNATIONAL_GREENHOUSE_TOKENS == EXPECTED_INTERNATIONAL_GREENHOUSE_TOKENS
    assert catalog_tokens == (
        EXPECTED_INTERNATIONAL_GREENHOUSE_TOKENS
        | EXPECTED_UNKNOWN_GREENHOUSE_TOKENS
    )
    for token in catalog_tokens:
        expected = (
            "international"
            if token in EXPECTED_INTERNATIONAL_GREENHOUSE_TOKENS
            else "unknown"
        )
        assert classify_company("Conflicting display name", source=f"Greenhouse:{token}") == expected


def test_greenhouse_source_token_takes_priority_over_conflicting_company_name() -> None:
    assert classify_company("华为", source="Greenhouse:mongodb") == "international"
    assert classify_company("MongoDB", source="Greenhouse:unreviewed-board") == "unknown"
    assert classify_company("华为", source="Greenhouse:unreviewed-board") == "unknown"


def test_unknown_company_is_not_inferred_from_location_language_or_url() -> None:
    assert classify_company("Unknown Shanghai Technology") == "unknown"
    assert classify_company("未知公司", source="Official website:example.cn") == "unknown"


def test_reviewed_sets_and_labels_are_stable() -> None:
    assert len(DOMESTIC_COMPANIES) >= 50
    assert set(DOMESTIC_COMPANY_ALIASES.values()) <= DOMESTIC_COMPANIES
    assert INTERNATIONAL_CAMPUS_COMPANIES == {"西门子", "西门子医疗", "博世", "施耐德电气"}
    assert scope_label("domestic") == "国内企业"
    assert scope_label("international") == "外企 / 国际企业"
    assert scope_label("unknown") == "未分类"


def test_scope_filter_keeps_each_group_separate_and_preserves_order() -> None:
    jobs = [
        JobPosting(
            title="国内岗位",
            company="华为",
            job_url="https://example.com/domestic",
            source="Official",
        ),
        JobPosting(
            title="外企岗位",
            company="MongoDB",
            job_url="https://example.com/international",
            source="Greenhouse:mongodb",
        ),
        JobPosting(
            title="待分类岗位",
            company="Example",
            job_url="https://example.com/unknown",
            source="Official",
        ),
    ]

    assert [job.title for job in filter_jobs_by_scope(jobs, "domestic")] == ["国内岗位"]
    assert [job.title for job in filter_jobs_by_scope(jobs, "international")] == ["外企岗位"]
    assert [job.title for job in filter_jobs_by_scope(jobs, "unknown")] == ["待分类岗位"]
    assert filter_jobs_by_scope(jobs, None) == jobs
