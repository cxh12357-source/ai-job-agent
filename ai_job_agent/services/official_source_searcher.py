"""Unified discovery for non-Greenhouse ATS boards and official career sites."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from ai_job_agent.models import JobPosting
from ai_job_agent.sources import (
    AshbySource,
    LeverSource,
    LiAutoCampusSource,
    SmartRecruitersSource,
)
from ai_job_agent.sources.campus_catalog import MAX_CAMPUS_SOURCES_PER_SEARCH
from ai_job_agent.sources.generic_official import (
    GenericOfficialSource,
    OfficialSourceError,
)
from ai_job_agent.sources.rendered_official import (
    RenderedOfficialError,
    discover_rendered_jobs,
)


class OfficialCareerSearchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DetectedCareerSource:
    kind: str
    account: str | None
    eu: bool = False


@dataclass(frozen=True, slots=True)
class OfficialCareerSearchResult:
    jobs: tuple[JobPosting, ...]
    sources_queried: tuple[str, ...]
    sources_succeeded: tuple[str, ...]
    failures: tuple[str, ...]
    warnings: tuple[str, ...]
    pages_fetched: int
    total_jobs_seen: int


def _first_path_part(url: str) -> str | None:
    parts = [unquote(item).strip() for item in urlsplit(url).path.split("/") if item.strip()]
    return parts[0] if parts else None


def detect_career_source(url: str) -> DetectedCareerSource:
    """Recognise public ATS board URLs; everything else uses safe generic DOM."""

    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError as exc:
        raise OfficialCareerSearchError("招聘页面 URL 格式无效") from exc
    host = (parts.hostname or "").rstrip(".").casefold()
    if parts.scheme.casefold() not in {"http", "https"} or not host:
        raise OfficialCareerSearchError("请输入完整的企业招聘页面 URL")
    if parts.username is not None or parts.password is not None:
        raise OfficialCareerSearchError("招聘页面 URL 不能包含账号或口令")

    if host in {"jobs.lever.co", "jobs.eu.lever.co"}:
        return DetectedCareerSource("lever", _first_path_part(url), host.startswith("jobs.eu."))
    if host in {"api.lever.co", "api.eu.lever.co"}:
        path = [item for item in parts.path.split("/") if item]
        account = path[2] if len(path) >= 3 and path[:2] == ["v0", "postings"] else None
        return DetectedCareerSource("lever", account, host.startswith("api.eu."))
    if host == "jobs.ashbyhq.com":
        return DetectedCareerSource("ashby", _first_path_part(url))
    if host == "api.ashbyhq.com":
        path = [item for item in parts.path.split("/") if item]
        account = path[2] if len(path) >= 3 and path[:2] == ["posting-api", "job-board"] else None
        return DetectedCareerSource("ashby", account)
    if host in {
        "careers.smartrecruiters.com",
        "jobs.smartrecruiters.com",
        "www.smartrecruiters.com",
    }:
        return DetectedCareerSource("smartrecruiters", _first_path_part(url))
    if host == "api.smartrecruiters.com":
        path = [item for item in parts.path.split("/") if item]
        account = path[2] if len(path) >= 3 and path[:2] == ["v1", "companies"] else None
        return DetectedCareerSource("smartrecruiters", account)
    if host in {"lixiang.com", "www.lixiang.com"} and (
        parts.path == "/employ"
        or parts.path.startswith("/employ/")
        or parts.path.startswith(
            "/osd-hr-recruitment-website/v1/recruit/school/"
        )
    ):
        return DetectedCareerSource("liauto-campus", "lixiang")
    return DetectedCareerSource("generic", None)


def _require_account(detected: DetectedCareerSource) -> str:
    account = str(detected.account or "").strip()
    if not account:
        raise OfficialCareerSearchError("无法从该 ATS 地址识别公司/职位板标识")
    return account


def _search_one(url: str, *, company: str | None, max_jobs: int) -> OfficialCareerSearchResult:
    detected = detect_career_source(url)
    label = f"{detected.kind}:{detected.account or urlsplit(url).hostname or 'unknown'}"
    if detected.kind == "lever":
        source = LeverSource(
            _require_account(detected),
            eu=detected.eu,
            company=company,
            max_jobs=max_jobs,
            max_pages=5,
        )
        report = source.discover()
        failures = tuple(str(item) for item in report.failures)
        return OfficialCareerSearchResult(
            report.jobs,
            (label,),
            (label,) if report.jobs or not failures else (),
            failures,
            ("结果已达到本轮岗位上限" if report.truncated else "",) if report.truncated else (),
            report.pages_fetched,
            report.total_seen,
        )
    if detected.kind == "ashby":
        source = AshbySource(
            _require_account(detected), company=company, max_jobs=max_jobs
        )
        report = source.discover()
        failures = tuple(str(item) for item in report.failures)
        return OfficialCareerSearchResult(
            report.jobs,
            (label,),
            (label,) if report.jobs or not failures else (),
            failures,
            ("结果已达到本轮岗位上限" if report.truncated else "",) if report.truncated else (),
            report.pages_fetched,
            report.total_seen,
        )
    if detected.kind == "smartrecruiters":
        source = SmartRecruitersSource(
            _require_account(detected),
            company=company,
            max_jobs=max_jobs,
            max_pages=5,
        )
        report = source.discover()
        failures = tuple(str(item) for item in report.failures)
        return OfficialCareerSearchResult(
            report.jobs,
            (label,),
            (label,) if report.jobs or not failures else (),
            failures,
            ("结果已达到本轮岗位上限" if report.truncated else "",) if report.truncated else (),
            report.pages_fetched,
            report.total_seen,
        )
    if detected.kind == "liauto-campus":
        source = LiAutoCampusSource(
            company=company,
            max_jobs=max_jobs,
            max_pages=5,
        )
        report = source.discover()
        failures = tuple(str(item) for item in report.failures)
        return OfficialCareerSearchResult(
            report.jobs,
            (label,),
            (label,) if report.jobs or not failures else (),
            failures,
            ("结果已达到本轮岗位上限" if report.truncated else "",)
            if report.truncated
            else (),
            report.pages_fetched,
            report.total_seen,
        )

    try:
        report = GenericOfficialSource(max_pages=8, max_jobs=max_jobs).discover(url)
    except OfficialSourceError as exc:
        raise OfficialCareerSearchError(str(exc)) from exc
    generic_jobs = report.jobs
    warnings = list(report.warnings)
    cloud_deployment = os.getenv("CLOUD_DEPLOYMENT", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not generic_jobs and cloud_deployment:
        warnings.append(
            "云端安全版未启动动态浏览器；此来源只保留静态公开页面的读取结果"
        )
    elif not generic_jobs:
        try:
            rendered = discover_rendered_jobs(
                url,
                company=company,
                max_jobs=max_jobs,
            )
            generic_jobs = rendered.jobs
            warnings.extend(rendered.warnings)
        except RenderedOfficialError as exc:
            warnings.append(str(exc))
    return OfficialCareerSearchResult(
        generic_jobs,
        (label,),
        (label,),
        (),
        tuple(warnings),
        len(report.pages_visited),
        len(generic_jobs),
    )


def search_official_career_urls(
    urls: Sequence[str],
    *,
    company: str | None = None,
    company_by_url: Mapping[str, str] | None = None,
    max_jobs_per_source: int = 200,
) -> OfficialCareerSearchResult:
    """Search explicitly supplied career pages within the campus catalog cap."""

    cleaned = tuple(dict.fromkeys(str(item).strip() for item in urls if str(item).strip()))
    if not cleaned:
        raise OfficialCareerSearchError("请至少输入一个企业官方招聘页面")
    if len(cleaned) > MAX_CAMPUS_SOURCES_PER_SEARCH:
        raise OfficialCareerSearchError(
            f"一次最多读取 {MAX_CAMPUS_SOURCES_PER_SEARCH} 个企业招聘页面"
        )
    max_jobs = max(1, min(int(max_jobs_per_source), 500))
    jobs_by_url: dict[str, JobPosting] = {}
    queried: list[str] = []
    succeeded: list[str] = []
    failures: list[str] = []
    warnings: list[str] = []
    pages_fetched = 0
    total_seen = 0

    def search_selected_url(
        url: str,
    ) -> tuple[str | None, OfficialCareerSearchResult]:
        reviewed_company = str((company_by_url or {}).get(url) or "").strip() or None
        source_company = reviewed_company or company
        return reviewed_company, _search_one(
            url,
            company=source_company,
            max_jobs=max_jobs,
        )

    # Submit only the explicitly selected, de-duplicated URLs. Results are merged
    # below in selection order so concurrency cannot change job de-duplication or
    # reporting order.
    with ThreadPoolExecutor(max_workers=min(4, len(cleaned))) as executor:
        searches = tuple(
            (url, executor.submit(search_selected_url, url)) for url in cleaned
        )
        for url, future in searches:
            try:
                reviewed_company, result = future.result()
            except (OfficialCareerSearchError, ValueError) as exc:
                queried.append(url)
                failures.append(f"{url}：{exc}")
                continue
            queried.extend(result.sources_queried)
            succeeded.extend(result.sources_succeeded)
            failures.extend(result.failures)
            warnings.extend(result.warnings)
            pages_fetched += result.pages_fetched
            total_seen += result.total_jobs_seen
            for job in result.jobs:
                if reviewed_company and job.company != reviewed_company:
                    job = job.model_copy(update={"company": reviewed_company})
                jobs_by_url.setdefault(job.job_url, job)

    if not jobs_by_url and failures:
        raise OfficialCareerSearchError("；".join(failures[:3]))
    return OfficialCareerSearchResult(
        jobs=tuple(jobs_by_url.values()),
        sources_queried=tuple(queried),
        sources_succeeded=tuple(succeeded),
        failures=tuple(failures),
        warnings=tuple(warnings),
        pages_fetched=pages_fetched,
        total_jobs_seen=total_seen,
    )


__all__ = [
    "DetectedCareerSource",
    "OfficialCareerSearchError",
    "OfficialCareerSearchResult",
    "detect_career_source",
    "search_official_career_urls",
]
