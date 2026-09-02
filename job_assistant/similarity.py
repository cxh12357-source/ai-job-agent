from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .matching import SKILL_TERMS, _hits, _norm
from .models import Job


# 职级和泛化岗位后缀不能单独证明两个岗位同类。
_LATIN_TITLE_NOISE = {
    "associate",
    "developer",
    "director",
    "engineer",
    "engineering",
    "expert",
    "head",
    "intern",
    "internship",
    "jr",
    "junior",
    "lead",
    "manager",
    "principal",
    "senior",
    "specialist",
    "sr",
    "staff",
}

_CHINESE_TITLE_NOISE = (
    "高级",
    "资深",
    "初级",
    "中级",
    "首席",
    "实习生",
    "实习",
    "工程师",
    "开发人员",
    "开发",
    "经理",
    "主管",
    "专员",
    "岗位",
)


def _latin_tokens(text: str) -> set[str]:
    # 分词后比较完整 token，不做英文子串匹配：AI 不会命中 paid。
    return set(re.findall(r"[a-z0-9]+(?:[+#.][a-z0-9+#.]*)*", _norm(text)))


def _chinese_tokens(text: str, *, strip_title_noise: bool = False) -> set[str]:
    tokens: set[str] = set()
    for segment in re.findall(r"[\u4e00-\u9fff]+", _norm(text)):
        if strip_title_noise:
            for noise in _CHINESE_TITLE_NOISE:
                segment = segment.replace(noise, "")
            if len(segment) >= 4 and segment.endswith(("师", "员")):
                segment = segment[:-1]
        if len(segment) < 2:
            continue
        if len(segment) <= 4:
            tokens.add(segment)
        tokens.update(segment[index : index + 2] for index in range(len(segment) - 1))
    return tokens


def _title_tokens(title: str) -> set[str]:
    latin = _latin_tokens(title) - _LATIN_TITLE_NOISE
    return latin | _chinese_tokens(title, strip_title_noise=True)


def _readable_shared_title_terms(terms: set[str]) -> tuple[str, ...]:
    latin = {term for term in terms if re.search(r"[a-z0-9]", term)}
    chinese = {term for term in terms if re.fullmatch(r"[\u4e00-\u9fff]+", term)}
    # 用最长的中文共同短语做解释，不展示“据分”之类跨词双字组合。
    readable_chinese = {
        term
        for term in chinese
        if len(term) >= 3 and not any(term != other and term in other for other in chinese)
    }
    if not readable_chinese:
        known_short_terms = {
            _norm(skill)
            for skill in SKILL_TERMS
            if re.fullmatch(r"[\u4e00-\u9fff]+", _norm(skill))
        }
        readable_chinese = chinese & known_short_terms
    return tuple(sorted(latin | readable_chinese))


def _text_tokens(text: str) -> set[str]:
    return _latin_tokens(text) | _chinese_tokens(text)


def _dice(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return 2 * len(left & right) / (len(left) + len(right))


def _canonical_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
    except (TypeError, ValueError):
        return ""
    if not parsed.scheme or not parsed.netloc:
        return ""
    # 跟踪查询参数不应让同一职位被当成“相似岗位”。
    return urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path.rstrip("/"), "", "")
    )


def _is_same_job(reference: Job, candidate: Job) -> bool:
    if reference.source == candidate.source and reference.id == candidate.id:
        return True
    reference_url = _canonical_url(reference.url)
    return bool(reference_url and reference_url == _canonical_url(candidate.url))


@dataclass(frozen=True, slots=True)
class SimilarJob:
    job: Job
    score: int
    reasons: tuple[str, ...] = field(default_factory=tuple)
    shared_title_terms: tuple[str, ...] = field(default_factory=tuple)
    shared_skills: tuple[str, ...] = field(default_factory=tuple)

    @property
    def similarity_score(self) -> int:
        """便于 UI 使用的显式别名，``score`` 保持与匹配结果风格一致。"""
        return self.score

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job.id,
            "title": self.job.title,
            "company": self.job.company,
            "location": self.job.location,
            "department": self.job.department,
            "similarity_score": self.score,
            "reasons": "；".join(self.reasons),
            "shared_title_terms": ", ".join(self.shared_title_terms),
            "shared_skills": ", ".join(self.shared_skills),
            "salary_text": self.job.salary_text,
            "language": self.job.language,
            "url": self.job.url,
            "source": self.job.source,
        }


# 更通用的类名别名，便于外部代码表达“相似度结果”。
SimilarityResult = SimilarJob


def score_similarity(reference: Job, candidate: Job) -> SimilarJob:
    """计算两个岗位的 0–100 可解释相似度。"""
    reference_title = _title_tokens(reference.title)
    candidate_title = _title_tokens(candidate.title)
    title_overlap = reference_title & candidate_title
    shared_title = _readable_shared_title_terms(title_overlap)
    title_similarity = _dice(reference_title, candidate_title)
    title_points = round(55 * title_similarity)

    reasons: list[str] = []
    if shared_title:
        reasons.append(f"岗位名称共同要素：{', '.join(shared_title)}")
    elif title_points:
        reasons.append("岗位名称结构相近")

    department_points = 0
    reference_department = _norm(reference.department)
    candidate_department = _norm(candidate.department)
    if reference_department and candidate_department:
        if reference_department == candidate_department:
            department_points = 15
            reasons.append(f"同属部门：{reference.department.strip()}")
        else:
            department_similarity = _dice(
                _text_tokens(reference.department), _text_tokens(candidate.department)
            )
            department_points = round(15 * department_similarity)
            if department_points:
                reasons.append("部门信息相近")

    reference_skills = set(_hits(SKILL_TERMS, reference.searchable_text))
    candidate_skills = set(_hits(SKILL_TERMS, candidate.searchable_text))
    shared_skills = tuple(sorted(reference_skills & candidate_skills))
    skill_points = 0
    if shared_skills:
        # 以较小的技能集合为分母，避免详细 JD 因为写了更多技能被惩罚。
        skill_points = round(
            20 * len(shared_skills) / min(len(reference_skills), len(candidate_skills))
        )
        reasons.append(f"共同技能：{', '.join(shared_skills)}")

    location_points = 0
    reference_location = _norm(reference.location)
    candidate_location = _norm(candidate.location)
    if reference_location and candidate_location:
        if reference_location == candidate_location:
            location_points = 5
            reasons.append(f"工作地点相同：{reference.location.strip()}")
        elif _text_tokens(reference.location) & _text_tokens(candidate.location):
            location_points = 3
            reasons.append("工作地点部分重合")

    language_points = 0
    reference_language = _norm(reference.language)
    candidate_language = _norm(candidate.language)
    if reference_language and reference_language == candidate_language:
        language_points = 5
        reasons.append(f"岗位语言相同：{reference.language.strip()}")

    score = min(
        100,
        title_points
        + department_points
        + skill_points
        + location_points
        + language_points,
    )
    if not reasons:
        reasons.append("没有足够的同类岗位信号")
    return SimilarJob(
        job=candidate,
        score=score,
        reasons=tuple(reasons),
        shared_title_terms=shared_title,
        shared_skills=shared_skills,
    )


def find_similar_jobs(
    reference: Job,
    jobs: Iterable[Job],
    *,
    limit: int = 5,
    minimum_score: int = 20,
) -> list[SimilarJob]:
    """返回稳定排序的 top-N 同类岗位，并排除参考岗位本身。"""
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("limit 必须是整数")
    if limit <= 0:
        return []
    if isinstance(minimum_score, bool) or not isinstance(minimum_score, int):
        raise ValueError("minimum_score 必须是 0 到 100 的整数")
    if not 0 <= minimum_score <= 100:
        raise ValueError("minimum_score 必须在 0 到 100 之间")

    scored = (
        score_similarity(reference, candidate)
        for candidate in jobs
        if not _is_same_job(reference, candidate)
    )
    relevant = (result for result in scored if result.score >= minimum_score)
    ranked = sorted(
        relevant,
        key=lambda result: (
            -result.score,
            _norm(result.job.title),
            _norm(result.job.company),
            _norm(result.job.source),
            str(result.job.id),
            _canonical_url(result.job.url),
        ),
    )
    return ranked[:limit]


# 提供面向“推荐”和“排序”语义的同义入口，不复制实现。
recommend_similar_jobs = find_similar_jobs
rank_similar_jobs = find_similar_jobs


__all__ = [
    "SimilarJob",
    "SimilarityResult",
    "find_similar_jobs",
    "rank_similar_jobs",
    "recommend_similar_jobs",
    "score_similarity",
]
