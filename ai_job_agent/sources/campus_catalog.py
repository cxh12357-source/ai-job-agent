"""Verified official campus-recruitment entry pages.

This catalog stores employer-owned or employer-linked public entry points.  It
does not imply that every page exposes a stable API: JavaScript-heavy sites may
only be available as a safe official link when bounded DOM discovery finds no
jobs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from ai_job_agent.company_scope import CompanyScope


DiscoveryMode = Literal["automatic", "best_effort", "link_only"]
MAX_CAMPUS_SOURCES_PER_SEARCH = 24


@dataclass(frozen=True, slots=True)
class CampusCareerSite:
    company: str
    label: str
    url: str
    regions: tuple[str, ...]
    system: str
    discovery_mode: DiscoveryMode = "best_effort"
    keywords: tuple[str, ...] = ("校招", "应届生", "毕业生", "实习")
    company_scope: CompanyScope = "domestic"

    def __post_init__(self) -> None:
        parts = urlsplit(self.url)
        if parts.scheme.casefold() != "https" or not parts.hostname:
            raise ValueError("校园招聘入口必须是完整 HTTPS 地址")
        if parts.username is not None or parts.password is not None or parts.fragment:
            raise ValueError("校园招聘入口不能包含凭据或页面片段")
        if not self.company.strip() or not self.label.strip() or not self.regions:
            raise ValueError("校园招聘入口缺少公司、标签或地区")
        if self.discovery_mode not in {"automatic", "best_effort", "link_only"}:
            raise ValueError("未知的校园招聘发现模式")
        if self.company_scope not in {"domestic", "international"}:
            raise ValueError("校园招聘目录中的企业类型必须明确")


CAMPUS_CAREER_SITES: tuple[CampusCareerSite, ...] = (
    CampusCareerSite(
        "字节跳动",
        "字节跳动 · 校园招聘",
        "https://jobs.bytedance.com/campus/",
        ("中国大陆", "全球"),
        "企业自建/飞书招聘技术栈",
        "link_only",
    ),
    CampusCareerSite(
        "华为",
        "华为 · 校园招聘",
        "https://career.huawei.com/cn/campus-recruitment",
        ("中国大陆",),
        "企业自建/定制",
        "best_effort",
    ),
    CampusCareerSite(
        "阿里巴巴",
        "阿里巴巴 · 校园职位",
        "https://campus-talent.alibaba.com/campus/position",
        ("中国大陆",),
        "企业自建/定制",
        "automatic",
    ),
    CampusCareerSite(
        "京东",
        "京东 · 校园招聘",
        "https://campus.jd.com/",
        ("中国大陆",),
        "企业自建/定制",
        "link_only",
    ),
    CampusCareerSite(
        "蔚来",
        "蔚来 · 校园招聘",
        "https://nio.jobs.feishu.cn/campus/",
        ("中国大陆",),
        "飞书招聘",
        "automatic",
    ),
    CampusCareerSite(
        "小鹏汽车",
        "小鹏汽车 · 校园招聘",
        "https://xiaopeng.jobs.feishu.cn/campus/",
        ("中国大陆",),
        "飞书招聘",
        "link_only",
    ),
    CampusCareerSite(
        "理想汽车",
        "理想汽车 · 校园招聘",
        "https://www.lixiang.com/employ/campus/list.html?fromJob=1",
        ("中国大陆",),
        "企业自建/定制",
        "automatic",
    ),
    CampusCareerSite(
        "小米",
        "小米 · 校园招聘",
        "https://hr.xiaomi.com/website/campus.html",
        ("中国大陆", "全球"),
        "企业自建动态站",
        "link_only",
    ),
    CampusCareerSite(
        "腾讯",
        "腾讯 · 校园招聘",
        "https://join.qq.com/",
        ("中国大陆", "全球"),
        "企业自建动态站",
        "link_only",
    ),
    CampusCareerSite(
        "美团",
        "美团 · 校园招聘",
        "https://zhaopin.meituan.com/web/campus",
        ("中国大陆",),
        "企业自建动态站",
        "best_effort",
    ),
    CampusCareerSite(
        "百度",
        "百度 · 校园招聘",
        "https://talent.baidu.com/jobs/list",
        ("中国大陆",),
        "企业自建动态站",
        "best_effort",
    ),
    CampusCareerSite(
        "网易",
        "网易 · 校园招聘",
        "https://campus.163.com/",
        ("中国大陆",),
        "网易 EHR / 动态站",
        "link_only",
    ),
    CampusCareerSite(
        "快手",
        "快手 · 校园招聘",
        "https://campus.kuaishou.cn/",
        ("中国大陆",),
        "企业动态站（robots 限制自动读取）",
        "link_only",
    ),
    CampusCareerSite(
        "滴滴",
        "滴滴 · 校园招聘",
        "https://talent.didiglobal.com/campus",
        ("中国大陆", "全球"),
        "企业自建动态站",
        "link_only",
    ),
    CampusCareerSite(
        "联想",
        "联想 · 校园招聘",
        "https://talent.lenovo.com.cn/position?projectType=1",
        ("中国大陆",),
        "企业自建动态站",
        "link_only",
    ),
    CampusCareerSite(
        "海尔",
        "海尔 · 校园招聘",
        "https://maker.haier.net/client/campus/activityindex.html",
        ("中国大陆", "全球"),
        "企业自建校园站",
        "automatic",
    ),
    CampusCareerSite(
        "海信",
        "海信 · 校园招聘",
        "https://jobs.hisense.com/campus/jobs",
        ("中国大陆",),
        "北森招聘（robots 限制自动读取）",
        "link_only",
    ),
    CampusCareerSite(
        "OPPO",
        "OPPO · 校园招聘",
        "https://careers.oppo.com/university/oppo/campus/post",
        ("中国大陆",),
        "企业自建动态站",
        "link_only",
    ),
    CampusCareerSite(
        "vivo",
        "vivo · 校园招聘",
        "https://hr-campus.vivo.com/campus/jobs",
        ("中国大陆",),
        "北森招聘",
        "link_only",
    ),
    CampusCareerSite(
        "大疆创新",
        "大疆创新 · 校园招聘",
        "https://careers.dji.com/zh-CN/campus/hot-jobs",
        ("中国大陆", "全球"),
        "企业自建动态站",
        "best_effort",
    ),
    CampusCareerSite(
        "比亚迪",
        "比亚迪 · 校园招聘",
        "https://job.byd.com/portal/mobile/school-home",
        ("中国大陆", "全球"),
        "企业自建动态站",
        "best_effort",
    ),
    CampusCareerSite(
        "中兴通讯",
        "中兴通讯 · 校园招聘",
        "https://job.zte.com.cn/cn/",
        ("中国大陆", "全球"),
        "企业自建招聘站",
        "automatic",
    ),
    CampusCareerSite(
        "美的集团",
        "美的集团 · 校园招聘",
        "https://careers.midea.com/recruit-school-out/",
        ("中国大陆", "全球"),
        "企业自建动态站",
        "best_effort",
    ),
    CampusCareerSite(
        "TCL",
        "TCL · 校园招聘",
        "https://zhaopin.tcl.com/campus/recruiting.html",
        ("中国大陆", "全球"),
        "企业自建招聘站",
        "automatic",
    ),
    CampusCareerSite(
        "海康威视",
        "海康威视 · 校园招聘",
        "https://campushr.hikvision.com/school",
        ("中国大陆", "全球"),
        "企业自建动态站",
        "best_effort",
    ),
    CampusCareerSite(
        "长安汽车",
        "长安汽车 · 校园招聘",
        "https://changan.zhiye.com/campus?c=1",
        ("中国大陆",),
        "北森招聘企业站",
        "best_effort",
    ),
    CampusCareerSite(
        "上汽集团",
        "上汽集团 · 校园招聘",
        "https://www.saicmotor.com/chinese/rlzy/rcxq/xyzp/index_xz1.shtml",
        ("中国大陆", "全球"),
        "企业官网校园招聘页",
        "best_effort",
    ),
    CampusCareerSite(
        "广汽集团",
        "广汽集团 · 人才招聘",
        "https://www.gacgroup.com/cn/talent",
        ("中国大陆", "全球"),
        "企业官网人才页",
        "best_effort",
    ),
    CampusCareerSite(
        "商汤科技",
        "商汤科技 · 校园招聘",
        "https://hr.sensetime.com/",
        ("中国大陆", "全球"),
        "企业自建动态站",
        "best_effort",
    ),
    CampusCareerSite(
        "寒武纪",
        "寒武纪 · 校园招聘",
        "https://joinus.cambricon.com/campus_apply/cambricon/1112/",
        ("中国大陆",),
        "Moka 企业招聘站",
        "best_effort",
    ),
    CampusCareerSite(
        "小红书",
        "小红书 · 校园招聘",
        "https://job.xiaohongshu.com/campus/position?campusRecruitTypes=term_regular",
        ("中国大陆", "全球"),
        "企业自建动态站",
        "automatic",
    ),
    CampusCareerSite(
        "哔哩哔哩",
        "哔哩哔哩 · 校园招聘",
        "https://www.bilibili.com/blackboard/join-list.html",
        ("中国大陆",),
        "企业官网招聘页",
        "best_effort",
    ),
    CampusCareerSite(
        "科大讯飞",
        "科大讯飞 · 校园招聘",
        "https://iflytek.zhiye.com/campus/jobs",
        ("中国大陆",),
        "北森招聘企业站",
        "best_effort",
    ),
    CampusCareerSite(
        "中国工商银行",
        "中国工商银行 · 校园招聘",
        "https://job.icbc.com.cn/",
        ("中国大陆", "全球"),
        "企业自建招聘站",
        "best_effort",
    ),
    CampusCareerSite(
        "中国农业银行",
        "中国农业银行 · 校园招聘",
        "https://career.abchina.com.cn/build/index.html",
        ("中国大陆", "全球"),
        "企业自建招聘站",
        "best_effort",
    ),
    CampusCareerSite(
        "中国建设银行",
        "中国建设银行 · 校园招聘",
        "https://e.ccb.com/cn/job/index.html",
        ("中国大陆", "全球"),
        "企业自建招聘站",
        "best_effort",
    ),
    CampusCareerSite(
        "招商银行",
        "招商银行 · 校园招聘",
        "https://career.cmbchina.com/campus/home",
        ("中国大陆", "全球"),
        "企业自建招聘站",
        "best_effort",
    ),
    CampusCareerSite(
        "交通银行",
        "交通银行 · 校园招聘",
        "https://job.bankcomm.com/",
        ("中国大陆", "全球"),
        "企业自建招聘站",
        "best_effort",
    ),
    CampusCareerSite(
        "中国平安",
        "中国平安 · 校园招聘",
        "https://campus.pingan.com/career",
        ("中国大陆",),
        "企业自建动态站",
        "best_effort",
    ),
    CampusCareerSite(
        "国家电网",
        "国家电网 · 校园招聘",
        "https://zhaopin.sgcc.com.cn/",
        ("中国大陆",),
        "企业自建招聘站",
        "best_effort",
    ),
    CampusCareerSite(
        "中国石油",
        "中国石油 · 校园招聘",
        "https://zhaopin.cnpc.com.cn/web/index.html",
        ("中国大陆", "全球"),
        "企业自建招聘站",
        "best_effort",
    ),
    CampusCareerSite(
        "中国石化",
        "中国石化 · 校园招聘",
        "https://job.sinopec.com/",
        ("中国大陆", "全球"),
        "企业自建动态站（校园招聘路由）",
        "best_effort",
    ),
    CampusCareerSite(
        "南方电网",
        "南方电网 · 校园招聘",
        "https://zhaopin.csg.cn/",
        ("中国大陆",),
        "企业自建动态站（校园招聘路由）",
        "best_effort",
    ),
    CampusCareerSite(
        "中国海油",
        "中国海油 · 校园招聘",
        "https://cnooc.zhaopin.com/",
        ("中国大陆", "全球"),
        "智联招聘企业站",
        "best_effort",
    ),
    CampusCareerSite(
        "国家能源集团",
        "国家能源集团 · 校园招聘",
        "https://zhaopin.chnenergy.com.cn/recTypeSerch?kinds=1&schType=6",
        ("中国大陆",),
        "企业自建招聘站",
        "best_effort",
    ),
    CampusCareerSite(
        "中国华能",
        "中国华能 · 校园招聘",
        "https://zhaopin.chng.com.cn/CampusRecruit",
        ("中国大陆",),
        "企业自建招聘站",
        "best_effort",
    ),
    CampusCareerSite(
        "中国移动",
        "中国移动 · 校园招聘",
        "https://job.10086.cn/personal/campus/campus_job_list.html",
        ("中国大陆",),
        "企业自建招聘站",
        "best_effort",
    ),
    CampusCareerSite(
        "中国电信",
        "中国电信 · 校园招聘",
        "https://job.chinatelecom.com.cn/wt/TELE/web/index/campus",
        ("中国大陆",),
        "企业自建招聘站",
        "automatic",
    ),
    CampusCareerSite(
        "中国联通",
        "中国联通 · 校园招聘",
        "https://zglt.zhaopin.com/scjobs/index.html",
        ("中国大陆",),
        "智联招聘企业站",
        "best_effort",
    ),
    CampusCareerSite(
        "伊利集团",
        "伊利集团 · 校园招聘",
        "https://www.yili.com/joinus",
        ("中国大陆", "全球"),
        "企业官网人才页",
        "best_effort",
    ),
    CampusCareerSite(
        "蒙牛乳业",
        "蒙牛乳业 · 校园招聘",
        "https://mengniu.zhiye.com/custom/xiaoyuan",
        ("中国大陆", "全球"),
        "智联招聘企业站",
        "best_effort",
    ),
    CampusCareerSite(
        "迈瑞医疗",
        "迈瑞医疗 · 校园招聘",
        "https://www.mindray.com/cn/career/campus-recruiting",
        ("中国大陆", "全球"),
        "企业官网校园招聘页",
        "best_effort",
    ),
    CampusCareerSite(
        "恒瑞医药",
        "恒瑞医药 · 校园招聘",
        "https://app.mokahr.com/campus-recruitment/hengrui/145997",
        ("中国大陆",),
        "Moka（企业官网链接）",
        "best_effort",
    ),
    CampusCareerSite(
        "国药集团",
        "国药集团 · 招聘公告",
        "https://www.sinopharm.com/zpgg.html",
        ("中国大陆", "全球"),
        "企业官网招聘公告",
        "best_effort",
    ),
    CampusCareerSite(
        "西门子",
        "西门子 · 中国校园招聘",
        "https://jobs.siemens.com.cn/siemens/position/index?recruitmentType=CAMPUSRECRUITMENT",
        ("中国大陆",),
        "企业定制 / Tupu360",
        "link_only",
        company_scope="international",
    ),
    CampusCareerSite(
        "西门子医疗",
        "西门子医疗 · 中国校园招聘",
        "https://jobs.siemens.com.cn/healthineers/position/index?recruitmentType=CAMPUSRECRUITMENT",
        ("中国大陆",),
        "企业定制 / Tupu360",
        "link_only",
        company_scope="international",
    ),
    CampusCareerSite(
        "博世",
        "博世 · 中国校园招聘",
        "https://app.mokahr.com/campus-recruitment/bosch/73873",
        ("中国大陆",),
        "Moka（博世官网链接）",
        "link_only",
        company_scope="international",
    ),
    CampusCareerSite(
        "施耐德电气",
        "施耐德电气 · Early Careers",
        "https://careers.se.com/early-careers?lang=zh-CN",
        ("中国大陆", "全球"),
        "企业全球招聘站",
        "link_only",
        company_scope="international",
    ),
)


CAMPUS_SITE_BY_LABEL = {site.label: site for site in CAMPUS_CAREER_SITES}
DEFAULT_CAMPUS_LABELS = (
    "阿里巴巴 · 校园职位",
    "蔚来 · 校园招聘",
    "海尔 · 校园招聘",
    "理想汽车 · 校园招聘",
    "华为 · 校园招聘",
    "美团 · 校园招聘",
    "百度 · 校园招聘",
    "大疆创新 · 校园招聘",
    "比亚迪 · 校园招聘",
    "中兴通讯 · 校园招聘",
    "美的集团 · 校园招聘",
    "TCL · 校园招聘",
    "海康威视 · 校园招聘",
    "长安汽车 · 校园招聘",
    "上汽集团 · 校园招聘",
    "广汽集团 · 人才招聘",
    "商汤科技 · 校园招聘",
    "寒武纪 · 校园招聘",
    "小红书 · 校园招聘",
    "哔哩哔哩 · 校园招聘",
    "科大讯飞 · 校园招聘",
    "国家电网 · 校园招聘",
    "中国移动 · 校园招聘",
    "中国电信 · 校园招聘",
)


def campus_sites_for_regions(regions: set[str] | None = None) -> tuple[CampusCareerSite, ...]:
    if not regions:
        return CAMPUS_CAREER_SITES
    return tuple(site for site in CAMPUS_CAREER_SITES if regions.intersection(site.regions))


def campus_sites_for_scope(scope: CompanyScope) -> tuple[CampusCareerSite, ...]:
    if scope == "unknown":
        return ()
    return tuple(site for site in CAMPUS_CAREER_SITES if site.company_scope == scope)


def resolve_campus_selection(labels: list[str] | tuple[str, ...]) -> tuple[CampusCareerSite, ...]:
    unique_labels = tuple(dict.fromkeys(str(label).strip() for label in labels if str(label).strip()))
    if len(unique_labels) > MAX_CAMPUS_SOURCES_PER_SEARCH:
        raise ValueError(f"每轮最多选择 {MAX_CAMPUS_SOURCES_PER_SEARCH} 个校园招聘渠道")
    unknown = [label for label in unique_labels if label not in CAMPUS_SITE_BY_LABEL]
    if unknown:
        raise ValueError("包含未核验的校园招聘渠道：" + "、".join(unknown))
    return tuple(CAMPUS_SITE_BY_LABEL[label] for label in unique_labels)


__all__ = [
    "CAMPUS_CAREER_SITES",
    "CAMPUS_SITE_BY_LABEL",
    "DEFAULT_CAMPUS_LABELS",
    "MAX_CAMPUS_SOURCES_PER_SEARCH",
    "CampusCareerSite",
    "campus_sites_for_regions",
    "campus_sites_for_scope",
    "resolve_campus_selection",
]
