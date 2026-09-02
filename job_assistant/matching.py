from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable

from .models import Criteria, Job, MatchResult

# 只用于可解释的简历/JD技能重合度，不对用户做职业方向推断。
SKILL_TERMS = (
    "python", "java", "javascript", "typescript", "react", "vue", "sql",
    "excel", "power bi", "tableau", "linux", "git", "docker", "kubernetes",
    "aws", "azure", "gcp", "机器学习", "深度学习", "大模型", "llm",
    "agent", "rag", "langchain", "langgraph", "数据分析", "数据建模",
    "项目管理", "产品设计", "用户研究", "市场分析", "销售", "售前",
    "客户成功", "英语", "日语", "c++", "go", "rust", "pytorch",
    "tensorflow", "pandas", "numpy", "spark", "hadoop", "api", "rest",
    "solidworks", "autocad", "cad", "cfd", "fea", "matlab", "热管理",
    "传热", "工程热力学", "流体力学", "机械设计", "智能制造", "制造工程",
    "工艺工程", "测试工程", "自动化", "电池", "新能源汽车", "根因分析",
    "thermal management", "heat transfer", "thermodynamics", "fluid mechanics",
    "mechanical design", "manufacturing", "process engineering", "testing",
    "battery", "electric vehicle", "root cause analysis", "business intelligence",
)

TERM_ALIASES = {
    "ai": ("artificial intelligence", "人工智能"),
    "人工智能": ("ai", "artificial intelligence"),
    "llm": ("large language model", "大模型"),
    "大模型": ("llm", "large language model"),
    "remote": ("远程", "work from home", "wfh"),
    "远程": ("remote", "work from home", "wfh"),
    "api": ("rest api", "restful api", "接口"),
    "上海": ("shanghai",),
    "北京": ("beijing",),
    "深圳": ("shenzhen",),
    "广州": ("guangzhou",),
    "苏州": ("suzhou",),
    "东莞": ("dongguan",),
    "香港": ("hong kong",),
    "新加坡": ("singapore",),
    "中国": ("china",),
    "数据分析实习生": (
        "data analyst intern", "data science intern", "business intelligence intern", "bi intern",
    ),
    "热管理工程师": ("thermal engineer", "thermal management engineer"),
    "机械工程师": ("mechanical engineer",),
    "制造工程师": ("manufacturing engineer",),
    "工艺工程师": ("process engineer",),
    "测试工程师": ("test engineer",),
    "自动化工程师": ("automation engineer",),
    "ai应用": ("ai engineer", "ai automation", "llm engineer", "data & ai"),
}

# 这类词在职位标题里代表职级，但在 JD 正文里经常只是“向资深同事学习”。
# 仅用标题/部门判断，避免把 “work with senior analysts” 这样的实习岗误删。
TITLE_ONLY_EXCLUSIONS = {
    "senior", "sr", "director", "principal", "staff", "lead", "manager",
    "head", "vp", "vice president", "高级", "资深", "总监", "经理", "主管",
}


def _norm(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text)).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _contains_term(text: str, term: str) -> bool:
    normalized_text = _norm(text)
    normalized_term = _norm(term)
    if not normalized_term:
        return False
    # Latin 技能词使用边界，避免 ai→paid、java→javascript、go→django。
    if re.fullmatch(r"[a-z0-9+#.]+(?: [a-z0-9+#.]+)*", normalized_term):
        pattern = rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])"
        return re.search(pattern, normalized_text) is not None
    return normalized_term in normalized_text


def _term_variants(term: str) -> tuple[str, ...]:
    normalized = _norm(term)
    aliases = TERM_ALIASES.get(normalized, ())
    return (term, *aliases)


def _hits(terms: Iterable[str], text: str) -> tuple[str, ...]:
    return tuple(
        term
        for term in terms
        if any(_contains_term(text, variant) for variant in _term_variants(term))
    )


def _excluded_hits(job: Job, terms: Iterable[str]) -> tuple[str, ...]:
    title_and_department = " ".join((job.title, job.department))
    hits: list[str] = []
    for term in terms:
        normalized = _norm(term)
        haystack = (
            title_and_department
            if normalized in TITLE_ONLY_EXCLUSIONS
            else job.searchable_text
        )
        if any(_contains_term(haystack, variant) for variant in _term_variants(term)):
            hits.append(term)
    return tuple(hits)


def _title_points(job: Job, criteria: Criteria) -> tuple[int, bool, str]:
    if not criteria.target_titles:
        return 30, True, "未限制岗位名称"
    title = _norm(job.title)
    exact = [
        term
        for term in criteria.target_titles
        if any(_contains_term(title, variant) for variant in _term_variants(term))
    ]
    if exact:
        return 30, True, f"岗位名称命中：{', '.join(exact)}"

    title_tokens = set(re.findall(r"[a-z0-9+#.]+|[\u4e00-\u9fff]{2,}", title))
    target_tokens = {
        token
        for term in criteria.target_titles
        for token in re.findall(r"[a-z0-9+#.]+|[\u4e00-\u9fff]{2,}", _norm(term))
    }
    overlap = title_tokens & target_tokens
    if overlap:
        return 15, True, f"岗位名称部分命中：{', '.join(sorted(overlap))}"
    return 0, False, "岗位名称不符合目标"


def _location_points(job: Job, criteria: Criteria) -> tuple[int, bool, str]:
    if not criteria.locations:
        return 15, True, "未限制工作地点"
    hits = _hits(criteria.locations, job.location)
    if hits:
        return 15, True, f"地点命中：{', '.join(hits)}"
    return 0, False, "工作地点不符合条件"


_PERIOD_ALIASES = {
    "month": "month",
    "monthly": "month",
    "per month": "month",
    "/month": "month",
    "月": "month",
    "月薪": "month",
    "每月": "month",
    "/月": "month",
    "year": "year",
    "yearly": "year",
    "annual": "year",
    "annually": "year",
    "per year": "year",
    "/year": "year",
    "年": "year",
    "年薪": "year",
    "每年": "year",
    "/年": "year",
    "hour": "hour",
    "hourly": "hour",
    "per hour": "hour",
    "/hour": "hour",
    "小时": "hour",
    "时薪": "hour",
    "每小时": "hour",
    "/小时": "hour",
    "day": "day",
    "daily": "day",
    "per day": "day",
    "/day": "day",
    "天": "day",
    "日薪": "day",
    "每天": "day",
    "/天": "day",
}

_CURRENCY_ALIASES = {
    "RMB": "CNY",
    "CN¥": "CNY",
    "人民币": "CNY",
    "US$": "USD",
}

_UNKNOWN_POLICY_ALIASES = {
    "include": "include",
    "allow": "include",
    "keep": "include",
    "ignore": "include",
    "保留": "include",
    "包含": "include",
    "允许": "include",
    "review": "review",
    "manual_review": "review",
    "flag": "review",
    "待确认": "review",
    "人工确认": "review",
    "exclude": "exclude",
    "reject": "exclude",
    "strict": "exclude",
    "排除": "exclude",
    "拒绝": "exclude",
}


def _finite_salary(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _normalize_currency(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).strip().upper()
    return _CURRENCY_ALIASES.get(normalized, normalized)


def _normalize_period(value: str) -> str:
    normalized = _norm(value)
    return _PERIOD_ALIASES.get(normalized, normalized)


def _normalize_unknown_policy(value: str) -> str:
    normalized = _norm(value or "include").replace("-", "_")
    try:
        return _UNKNOWN_POLICY_ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(
            "unknown_salary_policy 必须是 include、review 或 exclude"
        ) from exc


def _format_salary_number(value: float) -> str:
    return f"{value:g}"


def _format_salary_range(
    minimum: float | None,
    maximum: float | None,
    currency: str = "",
    period: str = "",
) -> str:
    if minimum is not None and maximum is not None:
        number_text = (
            _format_salary_number(minimum)
            if minimum == maximum
            else f"{_format_salary_number(minimum)}–{_format_salary_number(maximum)}"
        )
    elif minimum is not None:
        number_text = f"≥{_format_salary_number(minimum)}"
    elif maximum is not None:
        number_text = f"≤{_format_salary_number(maximum)}"
    else:
        return "未公开"
    currency_text = _normalize_currency(currency)
    period_text = str(period).strip()
    prefix = f"{currency_text} " if currency_text else ""
    suffix = f"/{period_text}" if period_text else ""
    return f"{prefix}{number_text}{suffix}"


def _unknown_salary_decision(reason: str, policy_value: str) -> tuple[bool, str]:
    policy = _normalize_unknown_policy(policy_value)
    if policy == "exclude":
        return False, f"{reason}；按“排除未知薪资”策略不符合条件"
    if policy == "review":
        return True, f"{reason}；已保留，投递前需人工核对薪资"
    return True, f"{reason}；按“保留未知薪资”策略继续匹配"


def evaluate_salary(job: Job, criteria: Criteria) -> tuple[bool, str]:
    """根据岗位与用户的期望薪资区间返回可解释的资格判断。

    区间只在币种和计薪周期可比时直接比较；不猜测汇率、年终奖
    或文本中的 ``20k``，以免错误自动筛掉岗位。
    """
    expected_min = _finite_salary(criteria.expected_salary_min)
    expected_max = _finite_salary(criteria.expected_salary_max)
    if criteria.expected_salary_min is not None and expected_min is None:
        raise ValueError("expected_salary_min 必须是有限非负数")
    if criteria.expected_salary_max is not None and expected_max is None:
        raise ValueError("expected_salary_max 必须是有限非负数")
    if expected_min is None and expected_max is None:
        return True, "未限制薪资"
    if (
        expected_min is not None
        and expected_max is not None
        and expected_min > expected_max
    ):
        raise ValueError("expected_salary_min 不能大于 expected_salary_max")

    job_min = _finite_salary(job.salary_min)
    job_max = _finite_salary(job.salary_max)
    expected_text = _format_salary_range(
        expected_min,
        expected_max,
        criteria.salary_currency,
        criteria.salary_period,
    )

    if job_min is None and job_max is None:
        detail = f"（{job.salary_text.strip()}）" if job.salary_text.strip() else ""
        return _unknown_salary_decision(
            f"岗位未公开可比较的薪资区间{detail}，期望 {expected_text}",
            criteria.unknown_salary_policy,
        )
    if job_min is not None and job_max is not None and job_min > job_max:
        return _unknown_salary_decision(
            f"岗位薪资区间无效，期望 {expected_text}",
            criteria.unknown_salary_policy,
        )

    expected_currency = _normalize_currency(criteria.salary_currency)
    job_currency = _normalize_currency(job.currency)
    if expected_currency and job_currency != expected_currency:
        actual = job_currency or "未标注币种"
        return _unknown_salary_decision(
            f"岗位薪资币种为 {actual}，无法与 {expected_currency} 安全比较",
            criteria.unknown_salary_policy,
        )

    expected_period = _normalize_period(criteria.salary_period)
    job_period = _normalize_period(job.period)
    if expected_period and job_period != expected_period:
        actual = job.period.strip() or "未标注周期"
        return _unknown_salary_decision(
            f"岗位计薪周期为 {actual}，无法与 {criteria.salary_period} 安全比较",
            criteria.unknown_salary_policy,
        )

    job_text = _format_salary_range(job_min, job_max, job.currency, job.period)
    if expected_min is not None and job_max is not None and job_max < expected_min:
        return False, f"岗位薪资 {job_text} 低于期望 {expected_text}"
    if expected_max is not None and job_min is not None and job_min > expected_max:
        return False, f"岗位薪资 {job_text} 高于目标区间 {expected_text}"

    # 区间相交需要 job.max >= expected.min 且 job.min <= expected.max。
    # 当关键边界未公开时，只在另一个边界已能证明相交时放行。
    lower_overlap: bool | None = True
    if expected_min is not None:
        if job_max is not None:
            lower_overlap = job_max >= expected_min
        elif job_min is not None and job_min >= expected_min:
            lower_overlap = True
        else:
            lower_overlap = None

    upper_overlap: bool | None = True
    if expected_max is not None:
        if job_min is not None:
            upper_overlap = job_min <= expected_max
        elif job_max is not None and job_max <= expected_max:
            upper_overlap = True
        else:
            upper_overlap = None

    if lower_overlap is None or upper_overlap is None:
        return _unknown_salary_decision(
            f"岗位薪资 {job_text} 区间不完整，无法确认是否与期望 {expected_text} 相交",
            criteria.unknown_salary_policy,
        )
    return True, f"岗位薪资 {job_text} 与期望 {expected_text} 有重叠"


def score_job(job: Job, resume_text: str, criteria: Criteria) -> MatchResult:
    title_points, title_ok, title_reason = _title_points(job, criteria)
    location_points, location_ok, location_reason = _location_points(job, criteria)
    salary_ok, salary_reason = evaluate_salary(job, criteria)
    searchable = job.searchable_text

    required_hits = _hits(criteria.required_keywords, searchable)
    missing_required = tuple(
        term for term in criteria.required_keywords if term not in required_hits
    )
    required_points = (
        round(25 * len(required_hits) / len(criteria.required_keywords))
        if criteria.required_keywords
        else 25
    )

    preferred_hits = _hits(criteria.preferred_keywords, searchable)
    preferred_points = (
        round(20 * len(preferred_hits) / len(criteria.preferred_keywords))
        if criteria.preferred_keywords
        else 10
    )

    resume_skills = set(_hits(SKILL_TERMS, resume_text))
    job_skills = set(_hits(SKILL_TERMS, searchable))
    common_skills = sorted(resume_skills & job_skills)
    skill_points = min(10, len(common_skills) * 2) if job_skills else 5

    excluded_hits = _excluded_hits(job, criteria.excluded_keywords)
    score = min(
        100,
        title_points + location_points + required_points + preferred_points + skill_points,
    )
    eligible = bool(
        title_ok
        and location_ok
        and salary_ok
        and not missing_required
        and not excluded_hits
        and score >= criteria.minimum_score
    )

    reasons = [title_reason, location_reason, salary_reason]
    if required_hits:
        reasons.append(f"必需关键词命中：{', '.join(required_hits)}")
    if missing_required:
        reasons.append(f"缺少必需关键词：{', '.join(missing_required)}")
    if preferred_hits:
        reasons.append(f"加分关键词命中：{', '.join(preferred_hits)}")
    if common_skills:
        reasons.append(f"简历与岗位技能重合：{', '.join(common_skills)}")
    if excluded_hits:
        reasons.append(f"排除词命中：{', '.join(excluded_hits)}")
    if score < criteria.minimum_score:
        reasons.append(f"评分低于阈值 {criteria.minimum_score}")

    return MatchResult(
        job=job,
        score=score,
        eligible=eligible,
        reasons=tuple(reasons),
        matched_keywords=tuple(dict.fromkeys(required_hits + preferred_hits)),
        missing_required=missing_required,
        excluded_hits=excluded_hits,
        salary_eligible=salary_ok,
        salary_reason=salary_reason,
    )


def rank_jobs(
    jobs: Iterable[Job], resume_text: str, criteria: Criteria
) -> list[MatchResult]:
    ranked = sorted(
        (score_job(job, resume_text, criteria) for job in jobs),
        key=lambda item: (-int(item.eligible), -item.score, item.job.title.casefold()),
    )
    return ranked[: criteria.max_results]
