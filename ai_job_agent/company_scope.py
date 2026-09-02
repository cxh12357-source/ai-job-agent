"""Explicit company-origin classification used by search and result filters.

Company origin is never inferred from a job location, language, URL suffix or
listing host.  Only reviewed company names and source tokens are classified;
everything else stays ``unknown`` so the UI does not guess.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Literal, TypeVar

from ai_job_agent.models import JobPosting
from job_assistant.discovery import DEFAULT_BOARD_CATALOG


CompanyScope = Literal["domestic", "international", "unknown"]
_JobT = TypeVar("_JobT", bound=JobPosting)

DOMESTIC_COMPANIES = frozenset(
    {
        "字节跳动",
        "华为",
        "阿里巴巴",
        "京东",
        "蔚来",
        "小鹏汽车",
        "理想汽车",
        "小米",
        "腾讯",
        "美团",
        "百度",
        "网易",
        "快手",
        "滴滴",
        "联想",
        "海尔",
        "海信",
        "OPPO",
        "vivo",
        "大疆创新",
        "比亚迪",
        "中兴通讯",
        "美的集团",
        "TCL",
        "海康威视",
        "长安汽车",
        "上汽集团",
        "广汽集团",
        "商汤科技",
        "寒武纪",
        "小红书",
        "哔哩哔哩",
        "科大讯飞",
        "中国工商银行",
        "中国农业银行",
        "中国建设银行",
        "招商银行",
        "交通银行",
        "中国平安",
        "国家电网",
        "中国石油",
        "中国石化",
        "南方电网",
        "中国海油",
        "国家能源集团",
        "中国华能",
        "中国移动",
        "中国电信",
        "中国联通",
        "伊利集团",
        "蒙牛乳业",
        "迈瑞医疗",
        "恒瑞医药",
        "国药集团",
    }
)

INTERNATIONAL_CAMPUS_COMPANIES = frozenset(
    {"西门子", "西门子医疗", "博世", "施耐德电气"}
)

# This is deliberately an explicit allow-list, not "every board except known
# exceptions".  ``DEFAULT_BOARD_CATALOG`` audits whether a public board may be
# queried; it does not audit the employer's corporate origin.  Consequently a
# newly added or otherwise unreviewed board must remain ``unknown`` until this
# list is reviewed and updated separately.
INTERNATIONAL_GREENHOUSE_TOKENS = frozenset(
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
INTERNATIONAL_COMPANIES = frozenset(
    {
        *INTERNATIONAL_CAMPUS_COMPANIES,
        *(
            board.company
            for board in DEFAULT_BOARD_CATALOG
            if board.token in INTERNATIONAL_GREENHOUSE_TOKENS
        ),
        "Spotify",
        "Ashby",
        "SmartRecruiters",
    }
)

DOMESTIC_COMPANY_ALIASES = {
    "Alibaba Group": "阿里巴巴",
    "alibaba.com": "阿里巴巴",
    "ByteDance": "字节跳动",
    "bytedance.com": "字节跳动",
    "Huawei": "华为",
    "huawei.com": "华为",
    "JD.com": "京东",
    "Jingdong": "京东",
    "Li Auto": "理想汽车",
    "lixiang.com": "理想汽车",
    "Lenovo": "联想",
    "Meituan": "美团",
    "NIO": "蔚来",
    "OPPO": "OPPO",
    "Tencent": "腾讯",
    "Xiaomi": "小米",
    "Xiaopeng": "小鹏汽车",
    "XPeng": "小鹏汽车",
    "Baidu": "百度",
    "NetEase": "网易",
    "Kuaishou": "快手",
    "DiDi": "滴滴",
    "Didi Global": "滴滴",
    "Haier": "海尔",
    "Hisense": "海信",
    "vivo": "vivo",
    "DJI": "大疆创新",
    "DJI Technology": "大疆创新",
    "careers.dji.com": "大疆创新",
    "BYD": "比亚迪",
    "BYD Auto": "比亚迪",
    "job.byd.com": "比亚迪",
    "ZTE": "中兴通讯",
    "ZTE Corporation": "中兴通讯",
    "job.zte.com.cn": "中兴通讯",
    "Midea": "美的集团",
    "Midea Group": "美的集团",
    "careers.midea.com": "美的集团",
    "TCL Technology": "TCL",
    "zhaopin.tcl.com": "TCL",
    "Hikvision": "海康威视",
    "campushr.hikvision.com": "海康威视",
    "Changan Automobile": "长安汽车",
    "changan.zhiye.com": "长安汽车",
    "SAIC": "上汽集团",
    "SAIC Motor": "上汽集团",
    "saicmotor.com": "上汽集团",
    "GAC": "广汽集团",
    "GAC Group": "广汽集团",
    "gacgroup.com": "广汽集团",
    "SenseTime": "商汤科技",
    "sensetime.com": "商汤科技",
    "Cambricon": "寒武纪",
    "Cambricon Technologies": "寒武纪",
    "cambricon.com": "寒武纪",
    "Xiaohongshu": "小红书",
    "REDnote": "小红书",
    "job.xiaohongshu.com": "小红书",
    "Bilibili": "哔哩哔哩",
    "bilibili.com": "哔哩哔哩",
    "iFlytek": "科大讯飞",
    "iflytek.com": "科大讯飞",
    "ICBC": "中国工商银行",
    "Industrial and Commercial Bank of China": "中国工商银行",
    "job.icbc.com.cn": "中国工商银行",
    "Agricultural Bank of China": "中国农业银行",
    "ABChina": "中国农业银行",
    "career.abchina.com.cn": "中国农业银行",
    "China Construction Bank": "中国建设银行",
    "CCB": "中国建设银行",
    "e.ccb.com": "中国建设银行",
    "China Merchants Bank": "招商银行",
    "CMB China": "招商银行",
    "career.cmbchina.com": "招商银行",
    "Bank of Communications": "交通银行",
    "BoCom": "交通银行",
    "job.bankcomm.com": "交通银行",
    "Ping An": "中国平安",
    "Ping An Group": "中国平安",
    "campus.pingan.com": "中国平安",
    "State Grid": "国家电网",
    "State Grid Corporation of China": "国家电网",
    "sgcc.com.cn": "国家电网",
    "CNPC": "中国石油",
    "PetroChina": "中国石油",
    "cnpc.com.cn": "中国石油",
    "Sinopec": "中国石化",
    "China Petroleum & Chemical Corporation": "中国石化",
    "sinopec.com": "中国石化",
    "China Southern Power Grid": "南方电网",
    "CSG": "南方电网",
    "csg.cn": "南方电网",
    "CNOOC": "中国海油",
    "China National Offshore Oil Corporation": "中国海油",
    "cnooc.com.cn": "中国海油",
    "CHN Energy": "国家能源集团",
    "China Energy Investment": "国家能源集团",
    "chnenergy.com.cn": "国家能源集团",
    "China Huaneng": "中国华能",
    "Huaneng Group": "中国华能",
    "chng.com.cn": "中国华能",
    "China Mobile": "中国移动",
    "CMCC": "中国移动",
    "10086.cn": "中国移动",
    "China Telecom": "中国电信",
    "chinatelecom.com.cn": "中国电信",
    "China Unicom": "中国联通",
    "chinaunicom.com.cn": "中国联通",
    "Yili": "伊利集团",
    "Yili Group": "伊利集团",
    "yili.com": "伊利集团",
    "Mengniu": "蒙牛乳业",
    "China Mengniu Dairy": "蒙牛乳业",
    "mengniu.com.cn": "蒙牛乳业",
    "Mindray": "迈瑞医疗",
    "mindray.com": "迈瑞医疗",
    "Hengrui Pharma": "恒瑞医药",
    "Jiangsu Hengrui Pharmaceuticals": "恒瑞医药",
    "hengrui.com": "恒瑞医药",
    "Sinopharm": "国药集团",
    "China National Pharmaceutical Group": "国药集团",
    "sinopharm.com": "国药集团",
}

# ``Official website:<host>`` is emitted by the generic and rendered official
# readers.  Only employer-owned or employer-specific hosts are allow-listed.
# Shared ATS hosts such as ``app.mokahr.com`` are deliberately excluded because
# a hostname alone cannot identify the employer safely.
DOMESTIC_OFFICIAL_DOMAINS = frozenset(
    {
        "jobs.bytedance.com",
        "career.huawei.com",
        "campus-talent.alibaba.com",
        "campus.jd.com",
        "nio.jobs.feishu.cn",
        "xiaopeng.jobs.feishu.cn",
        "www.lixiang.com",
        "hr.xiaomi.com",
        "join.qq.com",
        "zhaopin.meituan.com",
        "talent.baidu.com",
        "campus.163.com",
        "campus.kuaishou.cn",
        "talent.didiglobal.com",
        "talent.lenovo.com.cn",
        "maker.haier.net",
        "jobs.hisense.com",
        "careers.oppo.com",
        "hr-campus.vivo.com",
        "careers.dji.com",
        "job.byd.com",
        "job.zte.com.cn",
        "careers.midea.com",
        "zhaopin.tcl.com",
        "campushr.hikvision.com",
        "changan.zhiye.com",
        "www.saicmotor.com",
        "www.gacgroup.com",
        "hr.sensetime.com",
        "joinus.cambricon.com",
        "job.xiaohongshu.com",
        "www.bilibili.com",
        "iflytek.zhiye.com",
        "job.icbc.com.cn",
        "career.abchina.com.cn",
        "e.ccb.com",
        "career.cmbchina.com",
        "job.bankcomm.com",
        "campus.pingan.com",
        "zhaopin.sgcc.com.cn",
        "zhaopin.cnpc.com.cn",
        "job.sinopec.com",
        "zhaopin.csg.cn",
        "cnooc.zhaopin.com",
        "zhaopin.chnenergy.com.cn",
        "zhaopin.chng.com.cn",
        "job.10086.cn",
        "job.chinatelecom.com.cn",
        "zglt.zhaopin.com",
        "www.yili.com",
        "mengniu.zhiye.com",
        "www.mindray.com",
        "www.sinopharm.com",
    }
)
_INTERNATIONAL_ALIASES = {
    "boschgroup": "博世",
    "bosch": "博世",
    "schneiderelectric": "施耐德电气",
    "siemens": "西门子",
    "siemenshealthineers": "西门子医疗",
}


def _normalise(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)


_DOMESTIC_KEYS = {_normalise(name) for name in DOMESTIC_COMPANIES}
_DOMESTIC_ALIAS_KEYS = {
    _normalise(alias): canonical
    for alias, canonical in DOMESTIC_COMPANY_ALIASES.items()
}
_INTERNATIONAL_KEYS = {_normalise(name) for name in INTERNATIONAL_COMPANIES}


def classify_company(company: str | None, *, source: str | None = None) -> CompanyScope:
    """Return a reviewed company scope, or ``unknown`` without guessing."""

    source_text = str(source or "").strip()
    source_prefix, separator, source_value = source_text.partition(":")
    if source_prefix.casefold() == "greenhouse" and separator:
        token = source_value.strip().casefold()
        if token in INTERNATIONAL_GREENHOUSE_TOKENS:
            return "international"
        # A Greenhouse token is a stronger identity signal than an API/display
        # company name.  Unknown tokens never fall through to name aliases.
        return "unknown"
    if source_prefix.casefold() == "official website" and separator:
        source_domain = source_value.strip().casefold().rstrip(".")
        if source_domain in DOMESTIC_OFFICIAL_DOMAINS:
            return "domestic"
    if source_text.casefold() == "li auto campus":
        return "domestic"

    key = _normalise(company)
    if not key:
        return "unknown"
    if key in _DOMESTIC_KEYS or key in _DOMESTIC_ALIAS_KEYS:
        return "domestic"
    if key in _INTERNATIONAL_KEYS or key in _INTERNATIONAL_ALIASES:
        return "international"
    return "unknown"


def company_scope_for_job(job: JobPosting) -> CompanyScope:
    return classify_company(job.company, source=job.source)


def filter_jobs_by_scope(
    jobs: Iterable[_JobT], scope: CompanyScope | None
) -> list[_JobT]:
    """Keep jobs in one reviewed company group while preserving source order."""

    if scope is None:
        return list(jobs)
    return [job for job in jobs if company_scope_for_job(job) == scope]


def scope_label(scope: CompanyScope) -> str:
    return {
        "domestic": "国内企业",
        "international": "外企 / 国际企业",
        "unknown": "未分类",
    }[scope]


__all__ = [
    "CompanyScope",
    "DOMESTIC_COMPANY_ALIASES",
    "DOMESTIC_COMPANIES",
    "DOMESTIC_OFFICIAL_DOMAINS",
    "INTERNATIONAL_CAMPUS_COMPANIES",
    "INTERNATIONAL_COMPANIES",
    "INTERNATIONAL_GREENHOUSE_TOKENS",
    "classify_company",
    "company_scope_for_job",
    "filter_jobs_by_scope",
    "scope_label",
]
