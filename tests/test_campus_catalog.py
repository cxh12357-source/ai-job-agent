from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from ai_job_agent.sources.campus_catalog import (
    CAMPUS_CAREER_SITES,
    CAMPUS_SITE_BY_LABEL,
    DEFAULT_CAMPUS_LABELS,
    MAX_CAMPUS_SOURCES_PER_SEARCH,
    CampusCareerSite,
    campus_sites_for_scope,
    campus_sites_for_regions,
    resolve_campus_selection,
)


def test_catalog_entries_are_unique_safe_official_links() -> None:
    assert len(campus_sites_for_scope("domestic")) >= 50
    assert len({site.label for site in CAMPUS_CAREER_SITES}) == len(CAMPUS_CAREER_SITES)
    assert len({site.url for site in CAMPUS_CAREER_SITES}) == len(CAMPUS_CAREER_SITES)
    assert CAMPUS_SITE_BY_LABEL == {site.label: site for site in CAMPUS_CAREER_SITES}
    for site in CAMPUS_CAREER_SITES:
        parts = urlsplit(site.url)
        assert parts.scheme == "https"
        assert parts.hostname
        assert parts.username is None and parts.password is None
        assert parts.fragment == ""
        assert site.regions
        assert site.company_scope in {"domestic", "international"}


def test_core_verified_companies_and_defaults_are_present() -> None:
    companies = {site.company for site in CAMPUS_CAREER_SITES}
    assert MAX_CAMPUS_SOURCES_PER_SEARCH == 24
    assert {
        "字节跳动",
        "华为",
        "阿里巴巴",
        "京东",
        "蔚来",
        "小鹏汽车",
        "理想汽车",
        "大疆创新",
        "比亚迪",
        "中兴通讯",
        "美的集团",
        "TCL",
        "海康威视",
        "上汽集团",
        "商汤科技",
        "小红书",
        "中国工商银行",
        "国家电网",
        "中国石油",
        "中国移动",
        "伊利集团",
        "迈瑞医疗",
        "国药集团",
    } <= companies
    assert len(DEFAULT_CAMPUS_LABELS) == MAX_CAMPUS_SOURCES_PER_SEARCH
    assert len(set(DEFAULT_CAMPUS_LABELS)) == len(DEFAULT_CAMPUS_LABELS)
    assert set(DEFAULT_CAMPUS_LABELS) <= set(CAMPUS_SITE_BY_LABEL)
    automatic_companies = {
        site.company for site in CAMPUS_CAREER_SITES if site.discovery_mode == "automatic"
    }
    assert {"中兴通讯", "TCL", "小红书", "中国电信"} <= automatic_companies
    assert automatic_companies <= {
        "阿里巴巴",
        "蔚来",
        "海尔",
        "理想汽车",
        "中兴通讯",
        "TCL",
        "小红书",
        "中国电信",
    }
    assert {site.company for site in campus_sites_for_scope("international")} == {
        "西门子",
        "西门子医疗",
        "博世",
        "施耐德电气",
    }


def test_researched_domestic_portals_keep_conservative_discovery_modes() -> None:
    sites = {site.company: site for site in campus_sites_for_scope("domestic")}
    expected = {
        "华为": ("https://career.huawei.com/cn/campus-recruitment", "best_effort"),
        "美团": ("https://zhaopin.meituan.com/web/campus", "best_effort"),
        "大疆创新": ("https://careers.dji.com/zh-CN/campus/hot-jobs", "best_effort"),
        "比亚迪": ("https://job.byd.com/portal/mobile/school-home", "best_effort"),
        "中兴通讯": ("https://job.zte.com.cn/cn/", "automatic"),
        "美的集团": ("https://careers.midea.com/recruit-school-out/", "best_effort"),
        "TCL": ("https://zhaopin.tcl.com/campus/recruiting.html", "automatic"),
        "海康威视": ("https://campushr.hikvision.com/school", "best_effort"),
        "长安汽车": ("https://changan.zhiye.com/campus?c=1", "best_effort"),
        "招商银行": ("https://career.cmbchina.com/campus/home", "best_effort"),
        "国家电网": ("https://zhaopin.sgcc.com.cn/", "best_effort"),
        "中国石油": ("https://zhaopin.cnpc.com.cn/web/index.html", "best_effort"),
        "中国移动": (
            "https://job.10086.cn/personal/campus/campus_job_list.html",
            "best_effort",
        ),
        "小红书": (
            "https://job.xiaohongshu.com/campus/position?campusRecruitTypes=term_regular",
            "automatic",
        ),
        "中国电信": (
            "https://job.chinatelecom.com.cn/wt/TELE/web/index/campus",
            "automatic",
        ),
        "迈瑞医疗": (
            "https://www.mindray.com/cn/career/campus-recruiting",
            "best_effort",
        ),
        "恒瑞医药": (
            "https://app.mokahr.com/campus-recruitment/hengrui/145997",
            "best_effort",
        ),
    }

    assert expected.keys() <= sites.keys()
    for company, (url, mode) in expected.items():
        assert (sites[company].url, sites[company].discovery_mode) == (url, mode)


def test_region_filter_and_selection_preserve_catalog_order() -> None:
    mainland = campus_sites_for_regions({"中国大陆"})
    assert mainland
    assert all("中国大陆" in site.regions for site in mainland)
    selected = resolve_campus_selection([DEFAULT_CAMPUS_LABELS[1], DEFAULT_CAMPUS_LABELS[0]])
    assert [site.label for site in selected] == [
        DEFAULT_CAMPUS_LABELS[1],
        DEFAULT_CAMPUS_LABELS[0],
    ]


def test_selection_rejects_unknown_or_too_many_channels() -> None:
    with pytest.raises(ValueError, match="未核验"):
        resolve_campus_selection(["不存在 · 校园招聘"])
    with pytest.raises(ValueError, match=str(MAX_CAMPUS_SOURCES_PER_SEARCH)):
        resolve_campus_selection(
            [f"channel-{index}" for index in range(MAX_CAMPUS_SOURCES_PER_SEARCH + 1)]
        )


@pytest.mark.parametrize(
    "url",
    (
        "http://campus.example.com/jobs",
        "https://user:secret@campus.example.com/jobs",
        "https://campus.example.com/jobs#fragment",
    ),
)
def test_channel_model_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(ValueError):
        CampusCareerSite("Example", "Example · 校招", url, ("中国大陆",), "test")
