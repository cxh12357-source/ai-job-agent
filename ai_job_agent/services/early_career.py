"""Deterministic early-career classification for campus job discovery.

The classifier is deliberately conservative.  A normal public job board must
contain a positive campus/new-graduate signal before a role is included.  A
verified campus-only channel may omit that wording from individual job titles,
so those roles are accepted unless the page clearly asks for experienced or
senior candidates.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ai_job_agent.models import JobPosting


EARLY_CAREER_MARKERS = (
    "校园招聘",
    "校招",
    "应届",
    "毕业生",
    "实习",
    "管培生",
    "培训生",
    "储备干部",
    "new grad",
    "new graduate",
    "graduate ",
    "graduate program",
    "graduate programme",
    "graduate role",
    "campus hire",
    "campus recruitment",
    "intern",
    "internship",
    "trainee",
    "entry level",
    "entry-level",
)

SENIOR_TITLE_MARKERS = (
    "资深",
    "高级经理",
    "负责人",
    "总监",
    "专家",
    "首席",
    "senior",
    "staff",
    "principal",
    "director",
    "head of",
    "lead ",
    "manager",
    "architect",
)

_MINIMUM_EXPERIENCE_RE = re.compile(
    r"(?<!\d)(?:[3-9]|[1-9]\d)\s*(?:\+|年以上|年及以上|years?\+?|or more years?)",
    re.IGNORECASE,
)
_GRADUATION_COHORT_RE = re.compile(r"20(?:2[4-9]|3\d)\s*(?:届|graduates?)", re.IGNORECASE)


def _job_text(job: JobPosting) -> str:
    requirements = (
        "\n".join(job.requirements)
        if isinstance(job.requirements, list)
        else (job.requirements or "")
    )
    return "\n".join(
        value
        for value in (
            job.title,
            job.job_type,
            job.department,
            job.description,
            requirements,
            job.job_url,
            job.source,
        )
        if value
    ).casefold()


def has_early_career_signal(job: JobPosting) -> bool:
    """Return whether a posting explicitly targets students/new graduates."""

    text = _job_text(job)
    return _GRADUATION_COHORT_RE.search(text) is not None or any(
        marker in text for marker in EARLY_CAREER_MARKERS
    )


def is_clearly_experienced_role(job: JobPosting) -> bool:
    """Reject explicit senior titles or requirements of three-plus years."""

    title = job.title.casefold()
    if has_early_career_signal(job):
        # "Management Trainee" and "Graduate Software Engineer" are positive
        # even though a generic word can resemble a seniority marker.
        return False
    return any(marker in title for marker in SENIOR_TITLE_MARKERS) or bool(
        _MINIMUM_EXPERIENCE_RE.search(_job_text(job))
    )


def is_early_career_job(job: JobPosting, *, verified_campus_channel: bool = False) -> bool:
    """Classify one role for a fresh-graduate search.

    ``verified_campus_channel`` should only be true for a catalogued official
    campus recruitment entry page.
    """

    if is_clearly_experienced_role(job):
        return False
    if verified_campus_channel:
        return True
    return has_early_career_signal(job)


def filter_early_career_jobs(
    jobs: Iterable[JobPosting], *, verified_campus_channel: bool = False
) -> list[JobPosting]:
    return [
        job
        for job in jobs
        if is_early_career_job(job, verified_campus_channel=verified_campus_channel)
    ]


__all__ = [
    "EARLY_CAREER_MARKERS",
    "SENIOR_TITLE_MARKERS",
    "filter_early_career_jobs",
    "has_early_career_signal",
    "is_clearly_experienced_role",
    "is_early_career_job",
]
