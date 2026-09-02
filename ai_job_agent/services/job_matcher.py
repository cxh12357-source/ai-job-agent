"""Deterministic hybrid job matching with an optional bounded AI review."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from pydantic import BaseModel, Field

from ai_job_agent.models import (
    CandidateProfile,
    JobPosting,
    MatchAssessment,
    match_level_for_score,
    recommendation_for_score,
)
from ai_job_agent.services.early_career import (
    has_early_career_signal,
    is_clearly_experienced_role,
)
from ai_job_agent.services.profile_builder import _SKILL_ALIASES


WEIGHTS: dict[str, float] = {
    "skills": 35.0,
    "experience": 25.0,
    "education": 15.0,
    "projects": 10.0,
    "location": 5.0,
    "target_role": 10.0,
}

_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "ai": ("artificial intelligence", "machine learning", "deep learning", "llm", "ai ", "人工智能", "机器学习", "算法"),
    "software": ("software", "developer", "engineer", "backend", "frontend", "full stack", "研发", "开发", "工程师"),
    "data": ("data", "analytics", "business intelligence", "bi ", "数据", "分析", "商业智能"),
    "product": ("product", "产品"),
    "sales": ("sales", "business development", "account", "crm", "销售", "商务", "客户"),
    "marketing": ("marketing", "growth", "市场", "增长", "品牌"),
    "finance": ("finance", "financial", "accounting", "investment", "金融", "财务", "会计", "投资"),
    "research": ("research", "quant", "研究", "量化"),
    "operations": ("operations", "operation", "supply chain", "运营", "供应链"),
    "design": ("design", "ux", "ui ", "设计"),
}

_MAJOR_ALIASES: dict[str, tuple[str, ...]] = {
    "computer": ("computer science", "software engineering", "计算机", "软件工程"),
    "data": ("data science", "statistics", "statistical", "数据科学", "统计"),
    "engineering": ("engineering", "工程", "自动化", "电子", "机械"),
    "mathematics": ("mathematics", "applied math", "数学"),
    "business": ("business", "management", "工商管理", "管理学"),
    "finance": ("finance", "economics", "accounting", "金融", "经济", "会计"),
    "marketing": ("marketing", "市场营销"),
}

_LOCATION_ALIASES: dict[str, tuple[str, ...]] = {
    "beijing": ("beijing", "北京"),
    "shanghai": ("shanghai", "上海"),
    "shenzhen": ("shenzhen", "深圳"),
    "guangzhou": ("guangzhou", "广州"),
    "hangzhou": ("hangzhou", "杭州"),
    "nanjing": ("nanjing", "南京"),
    "suzhou": ("suzhou", "苏州"),
    "chengdu": ("chengdu", "成都"),
    "wuhan": ("wuhan", "武汉"),
    "hong kong": ("hong kong", "香港"),
    "singapore": ("singapore", "新加坡"),
    "remote": ("remote", "hybrid", "远程", "混合办公"),
}


def _joined(values: list[str | None]) -> str:
    return "\n".join(value for value in values if value)


def _experience_text(profile: CandidateProfile) -> str:
    values: list[str | None] = []
    for item in (profile.work_experience or []) + (profile.internships or []):
        values.extend((item.company, item.title, item.description))
        values.extend(item.achievements or [])
        values.extend(item.skills or [])
    return _joined(values)


def _project_text(profile: CandidateProfile) -> str:
    values: list[str | None] = []
    for project in profile.projects or []:
        values.extend((project.name, project.role, project.description))
        values.extend(project.achievements or [])
        values.extend(project.technologies or [])
    return _joined(values)


def _candidate_skill_text(profile: CandidateProfile) -> str:
    return _joined(
        list(profile.skills or [])
        + [_experience_text(profile), _project_text(profile)]
    )


def _contains(text: str, alias: str) -> bool:
    if re.fullmatch(r"[A-Za-z0-9+#. -]+", alias):
        return bool(
            re.search(
                rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])",
                text,
                flags=re.IGNORECASE,
            )
        )
    return alias.casefold() in text.casefold()


def _canonical_skills(text: str, explicit: list[str] | None = None) -> set[str]:
    source = "\n".join([text, *(explicit or [])])
    return {
        canonical
        for canonical, aliases in _SKILL_ALIASES.items()
        if any(_contains(source, alias) for alias in aliases)
    }


def _categories(text: str, aliases: dict[str, tuple[str, ...]]) -> set[str]:
    return {
        category
        for category, variants in aliases.items()
        if any(_contains(text, variant) for variant in variants)
    }


def _ratio(intersection: set[str], expected: set[str], *, neutral: float = 0.5) -> float:
    if not expected:
        return neutral
    return len(intersection) / len(expected)


def _skill_component(
    profile: CandidateProfile, job: JobPosting
) -> tuple[float, set[str], set[str]]:
    candidate = _canonical_skills(_candidate_skill_text(profile), profile.skills)
    job_skills = _canonical_skills(
        job.searchable_text(),
        (job.required_skills or []) + (job.preferred_skills or []),
    )
    matched = candidate & job_skills
    missing = job_skills - candidate
    return _ratio(matched, job_skills), matched, missing


def _experience_component(profile: CandidateProfile, job: JobPosting) -> float:
    candidate_text = _experience_text(profile)
    if not candidate_text:
        return 0.0
    candidate_categories = _categories(candidate_text, _ROLE_ALIASES)
    job_categories = _categories(job.searchable_text(), _ROLE_ALIASES)
    if not job_categories:
        return 0.5
    return _ratio(candidate_categories & job_categories, job_categories)


def _degree_rank(text: str) -> int | None:
    lowered = text.casefold()
    if any(term in lowered for term in ("phd", "ph.d", "doctorate", "博士")):
        return 3
    if any(term in lowered for term in ("master", "硕士")):
        return 2
    if any(term in lowered for term in ("bachelor", "undergraduate", "本科", "学士")):
        return 1
    return None


def _education_component(profile: CandidateProfile, job: JobPosting) -> float:
    job_text = job.searchable_text()
    required_rank = _degree_rank(job_text)
    candidate_text = _joined(
        [
            profile.school,
            profile.degree,
            profile.major,
            *[
                value
                for education in profile.education or []
                for value in (education.school, education.degree, education.major)
                if value
            ],
        ]
    )
    candidate_rank = _degree_rank(candidate_text)
    degree_score = (
        1.0
        if required_rank is None
        else (1.0 if candidate_rank is not None and candidate_rank >= required_rank else 0.0)
    )

    job_majors = _categories(job_text, _MAJOR_ALIASES)
    candidate_majors = _categories(candidate_text, _MAJOR_ALIASES)
    major_score = _ratio(candidate_majors & job_majors, job_majors, neutral=1.0)
    if required_rank is None and not job_majors:
        return 1.0
    if required_rank is None:
        return major_score
    if not job_majors:
        return degree_score
    return (degree_score + major_score) / 2


def _project_component(profile: CandidateProfile, job: JobPosting) -> float:
    project_text = _project_text(profile)
    if not project_text:
        return 0.0
    job_skills = _canonical_skills(job.searchable_text(), job.required_skills)
    project_skills = _canonical_skills(project_text)
    if job_skills:
        return _ratio(project_skills & job_skills, job_skills)
    job_categories = _categories(job.searchable_text(), _ROLE_ALIASES)
    project_categories = _categories(project_text, _ROLE_ALIASES)
    return _ratio(project_categories & job_categories, job_categories)


def _locations(text: str) -> set[str]:
    return _categories(text, _LOCATION_ALIASES)


def _location_component(profile: CandidateProfile, job: JobPosting) -> float:
    job_locations = _locations(job.location or "")
    if "remote" in job_locations:
        return 1.0
    preferences = _joined(list(profile.target_locations or []) + [profile.location])
    if not preferences:
        return 0.5
    preferred_locations = _locations(preferences)
    if not job_locations or not preferred_locations:
        normalized_job = re.sub(r"\s+", "", (job.location or "")).casefold()
        normalized_pref = re.sub(r"\s+", "", preferences).casefold()
        return 1.0 if normalized_job and normalized_job in normalized_pref else 0.0
    return 1.0 if job_locations & preferred_locations else 0.0


def _target_role_component(profile: CandidateProfile, job: JobPosting) -> float:
    if not profile.target_roles:
        return 0.5
    targets = "\n".join(profile.target_roles)
    target_categories = _categories(targets, _ROLE_ALIASES)
    job_categories = _categories(job.title, _ROLE_ALIASES)
    if target_categories and job_categories:
        return _ratio(target_categories & job_categories, job_categories)
    target_tokens = set(re.findall(r"[A-Za-z]{2,}|[\u4e00-\u9fff]{2,}", targets.casefold()))
    job_tokens = set(re.findall(r"[A-Za-z]{2,}|[\u4e00-\u9fff]{2,}", job.title.casefold()))
    return 1.0 if target_tokens & job_tokens else 0.0


class _LLMAdjustment(BaseModel):
    adjustment: float = Field(ge=-5, le=5)


class JobMatcher:
    def __init__(
        self,
        *,
        use_openai: bool | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.client = client
        self.use_openai = (
            bool(os.getenv("OPENAI_API_KEY")) if use_openai is None else use_openai
        )
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-5-mini")

    def match(self, profile: CandidateProfile, job: JobPosting) -> MatchAssessment:
        skill_score, matched, missing = _skill_component(profile, job)
        component_ratios = {
            "skills": skill_score,
            "experience": _experience_component(profile, job),
            "education": _education_component(profile, job),
            "projects": _project_component(profile, job),
            "location": _location_component(profile, job),
            "target_role": _target_role_component(profile, job),
        }
        breakdown = {
            name: round(component_ratios[name] * weight, 2)
            for name, weight in WEIGHTS.items()
        }
        base_score = round(sum(breakdown.values()), 1)
        adjustment = self._llm_adjustment(profile, job, base_score, breakdown)
        final_score = round(max(0.0, min(100.0, base_score + adjustment)), 1)

        advantages: list[str] = []
        risks: list[str] = []
        if matched:
            advantages.append("已具备岗位技能：" + "、".join(sorted(matched)))
        if component_ratios["experience"] >= 0.6:
            advantages.append("经历方向与岗位较一致")
        if component_ratios["education"] >= 0.8:
            advantages.append("学历或专业要求匹配")
        if component_ratios["target_role"] >= 0.8:
            advantages.append("岗位方向符合求职目标")
        if profile.career_stage in {"fresh_graduate", "internship"}:
            if has_early_career_signal(job):
                advantages.append("岗位明确面向应届生、毕业生或实习生")
            elif is_clearly_experienced_role(job):
                risks.append("岗位明确偏向资深或有多年经验的候选人")
        if missing:
            risks.append("简历未体现岗位技能：" + "、".join(sorted(missing)))
        if component_ratios["education"] == 0:
            risks.append("简历未证明满足岗位学历或专业要求")
        if component_ratios["location"] == 0:
            risks.append("工作地点与当前目标地点不一致")
        if not advantages:
            advantages.append("尚无足够结构化证据形成明确优势")

        reason = (
            f"规则基础分 {base_score:.1f}；技能 {breakdown['skills']:.1f}/35、"
            f"经历 {breakdown['experience']:.1f}/25、教育 {breakdown['education']:.1f}/15、"
            f"项目 {breakdown['projects']:.1f}/10、地点 {breakdown['location']:.1f}/5、"
            f"岗位方向 {breakdown['target_role']:.1f}/10。"
        )
        if adjustment:
            reason += f" AI 二次判断在限定范围内调整 {adjustment:+.1f} 分。"

        return MatchAssessment(
            match_score=final_score,
            match_level=match_level_for_score(final_score),
            base_score=base_score,
            llm_adjustment=adjustment,
            score_breakdown=breakdown,
            weights=dict(WEIGHTS),
            matched_skills=sorted(matched),
            missing_skills=sorted(missing),
            advantages=advantages,
            risks=risks,
            reason=reason,
            recommendation=recommendation_for_score(final_score),
        )

    def _llm_adjustment(
        self,
        profile: CandidateProfile,
        job: JobPosting,
        base_score: float,
        breakdown: dict[str, float],
    ) -> float:
        if not self.use_openai:
            return 0.0
        if self.client is None and not os.getenv("OPENAI_API_KEY"):
            return 0.0
        try:
            client = self.client
            if client is None:
                from openai import OpenAI

                client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
            safe_profile = {
                "skills": profile.skills,
                "degree": profile.degree,
                "major": profile.major,
                "experience": [
                    {
                        "title": item.title,
                        "description": item.description,
                        "skills": item.skills,
                    }
                    for item in (profile.work_experience or [])
                    + (profile.internships or [])
                ],
                "projects": [project.model_dump() for project in profile.projects or []],
                "target_roles": profile.target_roles,
                "target_locations": profile.target_locations,
            }
            response = client.responses.parse(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "复核基于固定权重得到的岗位匹配分。只依据给定事实，禁止补全经历。"
                            "你只能返回 -5 到 +5 的小幅校准；证据不足时返回 0。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "candidate": safe_profile,
                                "job": job.model_dump(),
                                "base_score": base_score,
                                "breakdown": breakdown,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                text_format=_LLMAdjustment,
                store=False,
            )
            parsed = response.output_parsed
            if parsed is None:
                return 0.0
            if not isinstance(parsed, _LLMAdjustment):
                parsed = _LLMAdjustment.model_validate(parsed)
            return round(max(-5.0, min(5.0, float(parsed.adjustment))), 1)
        except Exception:
            return 0.0


def match_job(
    profile: CandidateProfile,
    job: JobPosting,
    *,
    use_openai: bool | None = None,
    model: str | None = None,
    client: Any | None = None,
) -> MatchAssessment:
    return JobMatcher(
        use_openai=use_openai, model=model, client=client
    ).match(profile, job)


__all__ = ["JobMatcher", "WEIGHTS", "match_job"]
