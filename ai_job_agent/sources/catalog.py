"""Small verified catalog of official career entry pages.

The catalog is a convenience list, not a claim that every site exposes a
stable public API.  Self-built sites still go through the bounded generic
reader and may require the visible-browser fallback.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OfficialCareerSite:
    company: str
    label: str
    url: str
    system: str


OFFICIAL_CAREER_SITES: tuple[OfficialCareerSite, ...] = (
    OfficialCareerSite("小米", "小米 · 中国招聘", "https://xiaomi.jobs.f.mioffice.cn/index", "企业官网跳转/飞书招聘技术栈"),
    OfficialCareerSite("字节跳动", "字节跳动 · 社会招聘", "https://jobs.bytedance.com/experienced/position", "企业自建/飞书技术栈"),
    OfficialCareerSite("华为", "华为 · 社会招聘", "https://career.huawei.com/cn/social-recruitment", "企业自建/定制"),
    OfficialCareerSite("腾讯", "腾讯 · 职位搜索", "https://careers.tencent.com/search.html", "企业自建/定制"),
    OfficialCareerSite("阿里巴巴", "阿里巴巴 · 集团招聘", "https://talent.alibaba.com/", "企业自建/定制"),
    OfficialCareerSite("美团", "美团 · 官方招聘", "https://zhaopin.meituan.com/web/home", "企业自建/定制"),
    OfficialCareerSite("京东", "京东 · 社会招聘", "https://zhaopin.jd.com/", "企业自建/定制"),
    OfficialCareerSite("蔚来", "蔚来 · 中国社招", "https://nio.jobs.feishu.cn/index", "飞书招聘"),
    OfficialCareerSite("小鹏汽车", "小鹏 · 社会招聘", "https://xiaopeng.jobs.feishu.cn/index", "飞书招聘"),
    OfficialCareerSite("理想汽车", "理想 · 社会招聘", "https://www.lixiang.com/employ/social.html?fromJob=1", "企业自建/定制"),
    OfficialCareerSite("西门子", "西门子 · 中国职位", "https://jobs.siemens.com.cn/siemens/position/index", "企业定制/Tupu360"),
    OfficialCareerSite("西门子", "西门子 · 全球职位", "https://jobs.siemens.com/en_US/externaljobs/SearchJobs/", "Avature"),
    OfficialCareerSite("Spotify", "Spotify · Lever 职位板", "https://jobs.lever.co/spotify", "Lever"),
    OfficialCareerSite("Ashby", "Ashby · 官方职位板", "https://jobs.ashbyhq.com/Ashby", "Ashby"),
    OfficialCareerSite("SmartRecruiters", "SmartRecruiters · 官方职位板", "https://careers.smartrecruiters.com/SmartRecruiters", "SmartRecruiters"),
)


CAREER_SITE_BY_LABEL = {site.label: site for site in OFFICIAL_CAREER_SITES}


__all__ = ["CAREER_SITE_BY_LABEL", "OFFICIAL_CAREER_SITES", "OfficialCareerSite"]
