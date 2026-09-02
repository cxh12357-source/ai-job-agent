from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


def _clean_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = value.split(",")
    return tuple(str(item).strip() for item in value if str(item).strip())


def _optional_number(value: Any, field_name: str) -> float | None:
    """把配置中的可选薪资转换为有限非负数。

    数值单位由 ``salary_period`` 表达；这里不猜测 ``20k`` 之类的
    文本，避免在投递前做出错误的薪资判断。
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} 必须是数字")
    try:
        number = float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是数字") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field_name} 必须是有限非负数")
    return number


def _first_present(data: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in data:
            return data[name]
    return None


def _unknown_salary_policy(value: Any) -> str:
    normalized = str(value or "include").strip().casefold().replace("-", "_")
    aliases = {
        "include": "include",
        "allow": "include",
        "keep": "include",
        "ignore": "include",
        "保留": "include",
        "包含": "include",
        "允许": "include",
        "exclude": "exclude",
        "reject": "exclude",
        "strict": "exclude",
        "排除": "exclude",
        "拒绝": "exclude",
        "review": "review",
        "manual_review": "review",
        "flag": "review",
        "待确认": "review",
        "人工确认": "review",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(
            "unknown_salary_policy 必须是 include、review 或 exclude"
        ) from exc


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    title: str
    company: str
    location: str
    description: str
    url: str
    source: str
    department: str = ""
    updated_at: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    currency: str = ""
    period: str = ""
    salary_text: str = ""
    language: str = ""

    @property
    def searchable_text(self) -> str:
        return " ".join(
            (
                self.title,
                self.company,
                self.location,
                self.department,
                self.description,
                self.salary_text,
                self.language,
            )
        )


@dataclass(frozen=True, slots=True)
class Criteria:
    target_titles: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    required_keywords: tuple[str, ...] = ()
    preferred_keywords: tuple[str, ...] = ()
    excluded_keywords: tuple[str, ...] = ()
    minimum_score: int = 60
    max_results: int = 20
    expected_salary_min: float | None = None
    expected_salary_max: float | None = None
    salary_currency: str = ""
    salary_period: str = ""
    unknown_salary_policy: str = "include"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Criteria":
        minimum_score = int(data.get("minimum_score", 60))
        max_results = int(data.get("max_results", 20))
        if not 0 <= minimum_score <= 100:
            raise ValueError("minimum_score 必须在 0 到 100 之间")
        if not 1 <= max_results <= 100:
            raise ValueError("max_results 必须在 1 到 100 之间")
        expected_salary_min = _optional_number(
            _first_present(
                data,
                "expected_salary_min",
                "target_salary_min",
                "salary_min",
                "expected_salary",
                "target_salary",
            ),
            "expected_salary_min",
        )
        expected_salary_max = _optional_number(
            _first_present(
                data,
                "expected_salary_max",
                "target_salary_max",
                "salary_max",
            ),
            "expected_salary_max",
        )
        if (
            expected_salary_min is not None
            and expected_salary_max is not None
            and expected_salary_min > expected_salary_max
        ):
            raise ValueError("expected_salary_min 不能大于 expected_salary_max")

        salary_currency = str(
            _first_present(data, "salary_currency", "currency") or ""
        ).strip().upper()
        salary_period = str(
            _first_present(data, "salary_period", "period") or ""
        ).strip()
        unknown_salary_policy = _unknown_salary_policy(
            _first_present(data, "unknown_salary_policy", "salary_unknown_policy")
        )
        return cls(
            target_titles=_clean_list(data.get("target_titles")),
            locations=_clean_list(data.get("locations")),
            required_keywords=_clean_list(data.get("required_keywords")),
            preferred_keywords=_clean_list(data.get("preferred_keywords")),
            excluded_keywords=_clean_list(data.get("excluded_keywords")),
            minimum_score=minimum_score,
            max_results=max_results,
            expected_salary_min=expected_salary_min,
            expected_salary_max=expected_salary_max,
            salary_currency=salary_currency,
            salary_period=salary_period,
            unknown_salary_policy=unknown_salary_policy,
        )


@dataclass(frozen=True, slots=True)
class MatchResult:
    job: Job
    score: int
    eligible: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)
    matched_keywords: tuple[str, ...] = field(default_factory=tuple)
    missing_required: tuple[str, ...] = field(default_factory=tuple)
    excluded_hits: tuple[str, ...] = field(default_factory=tuple)
    salary_eligible: bool = True
    salary_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job.id,
            "title": self.job.title,
            "company": self.job.company,
            "location": self.job.location,
            "department": self.job.department,
            "salary_min": self.job.salary_min,
            "salary_max": self.job.salary_max,
            "currency": self.job.currency,
            "period": self.job.period,
            "salary_text": self.job.salary_text,
            "language": self.job.language,
            "score": self.score,
            "eligible": self.eligible,
            "salary_eligible": self.salary_eligible,
            "salary_reason": self.salary_reason,
            "matched_keywords": ", ".join(self.matched_keywords),
            "missing_required": ", ".join(self.missing_required),
            "excluded_hits": ", ".join(self.excluded_hits),
            "reasons": "；".join(self.reasons),
            "url": self.job.url,
            "source": self.job.source,
            "updated_at": self.job.updated_at,
        }
