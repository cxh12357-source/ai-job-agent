from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ai_job_agent.demo_data import demo_jobs
from ai_job_agent.models import CandidateProfile, JobPosting
from job_assistant.discovery import CHINA_APAC_BOARD_TOKENS


DEFAULT_GREENHOUSE_TOKENS = tuple(CHINA_APAC_BOARD_TOKENS)


class JobSearchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class JobSearchResult:
    """Jobs plus transparent source coverage for the Streamlit UI."""

    jobs: tuple[JobPosting, ...]
    mode: str
    boards_queried: tuple[str, ...] = ()
    boards_succeeded: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    cache_hits: tuple[str, ...] = ()
    total_jobs_seen: int = 0


def _from_legacy_job(job: object) -> JobPosting:
    return JobPosting(
        job_id=str(getattr(job, "id", "")),
        title=str(getattr(job, "title", "")),
        company=str(getattr(job, "company", "")),
        location=str(getattr(job, "location", "")) or None,
        job_type=None,
        department=str(getattr(job, "department", "")) or None,
        description=str(getattr(job, "description", "")),
        requirements=None,
        job_url=str(getattr(job, "url", "")),
        source=str(getattr(job, "source", "Greenhouse")),
        publish_date=str(getattr(job, "updated_at", "")) or None,
    )


def search_jobs_detailed(
    profile: CandidateProfile,
    *,
    demo_mode: bool,
    target_roles: Sequence[str] | None = None,
    target_locations: Sequence[str] | None = None,
    greenhouse_tokens: Sequence[str] | None = None,
    broad_search: bool = False,
    nationwide: bool = False,
    limit: int = 200,
) -> JobSearchResult:
    """Return jobs and source diagnostics from Demo or official boards.

    ``broad_search`` disables the coarse title gate so the explainable matcher,
    rather than a literal title substring, decides relevance. ``nationwide``
    disables the coarse city gate. These switches change recall; the score
    threshold remains a display filter and never changes how many boards are
    queried.
    """

    if demo_mode:
        jobs = tuple(demo_jobs())
        return JobSearchResult(
            jobs=jobs,
            mode="demo",
            boards_queried=("local-demo",),
            boards_succeeded=("local-demo",),
            total_jobs_seen=len(jobs),
        )
    try:
        from job_assistant.discovery import DiscoveryQuery, GreenhouseDiscovery

        roles = () if broad_search else tuple(target_roles or profile.target_roles or ())
        locations = (
            ()
            if nationwide
            else tuple(target_locations or profile.target_locations or ())
        )
        tokens = tuple(greenhouse_tokens or DEFAULT_GREENHOUSE_TOKENS)
        report = GreenhouseDiscovery().discover(
            DiscoveryQuery(
                resume_keywords=tuple(profile.skills or ()),
                locations=locations,
                target_titles=roles,
                minimum_keyword_hits=0,
                limit=max(1, min(int(limit), 500)),
            ),
            board_tokens=tokens,
        )
        return JobSearchResult(
            jobs=tuple(_from_legacy_job(job) for job in report.jobs),
            mode="official",
            boards_queried=tuple(report.boards_queried),
            boards_succeeded=tuple(report.boards_succeeded),
            failures=tuple(
                f"{failure.company}：{failure.message}" for failure in report.failures
            ),
            cache_hits=tuple(report.cache_hits),
            total_jobs_seen=int(report.total_jobs_seen),
        )
    except Exception as exc:
        raise JobSearchError("真实岗位读取失败；可切换 Demo Mode 继续体验") from exc


def search_jobs(
    profile: CandidateProfile,
    *,
    demo_mode: bool,
    target_roles: Sequence[str] | None = None,
    target_locations: Sequence[str] | None = None,
    greenhouse_tokens: Sequence[str] | None = None,
    broad_search: bool = False,
    nationwide: bool = False,
    limit: int = 200,
) -> list[JobPosting]:
    """Backward-compatible jobs-only view used by service callers."""

    result = search_jobs_detailed(
        profile,
        demo_mode=demo_mode,
        target_roles=target_roles,
        target_locations=target_locations,
        greenhouse_tokens=greenhouse_tokens,
        broad_search=broad_search,
        nationwide=nationwide,
        limit=limit,
    )
    return list(result.jobs)


__all__ = [
    "DEFAULT_GREENHOUSE_TOKENS",
    "JobSearchError",
    "JobSearchResult",
    "search_jobs",
    "search_jobs_detailed",
]
