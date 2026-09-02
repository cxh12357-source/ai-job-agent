"""Read-only adapter for Li Auto's public campus recruitment API."""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import quote

from ai_job_agent.models import JobPosting

from .base import (
    JobSourceAdapter,
    SourceDiscoveryResult,
    clean_text,
    join_unique,
    require_title,
    safe_https_url,
    stable_job_id,
)


class LiAutoCampusSource(JobSourceAdapter):
    """Fetch published campus jobs without calling login or application APIs."""

    source_name = "Li Auto Campus"
    default_page_size = 100
    maximum_page_size = 100

    _ACCOUNT = "lixiang"
    # The official project list identifies 25 as "2027校园招聘".
    _PROJECT_ID = 25
    _LIST_ENDPOINT = (
        "https://www.lixiang.com/"
        "osd-hr-recruitment-website/v1/recruit/school/job-page"
    )
    _DETAIL_BASE = "https://www.lixiang.com/employ/detail"

    def __init__(self, *, company: str | None = None, **kwargs: Any) -> None:
        # The account and endpoint are intentionally not caller-configurable: this
        # connector may only read Li Auto's official public HTTPS campus endpoint.
        super().__init__(self._ACCOUNT, company=company or "理想汽车", **kwargs)
        self.endpoint = self._LIST_ENDPOINT

    def discover(self) -> SourceDiscoveryResult:
        jobs: list[JobPosting] = []
        failures = []
        seen_ids: set[str] = set()
        pages_fetched = 0
        total_seen = 0
        truncated = False
        reported_total_pages: int | None = None
        reported_total_count: int | None = None

        for page_number in range(1, self.max_pages + 1):
            if len(jobs) >= self.max_jobs:
                truncated = True
                break
            try:
                payload = self._get_json(
                    self.endpoint,
                    params={
                        "page": page_number,
                        "page_size": self.page_size,
                        "project_id": self._PROJECT_ID,
                    },
                )
                data = self._page_data(payload)
            except Exception as exc:
                failures.append(self._failure("list", exc, page=page_number))
                break

            records = data["items"]
            pages_fetched += 1
            total_seen += len(records)
            reported_total_pages = _optional_nonnegative_int(data.get("total_pages"))
            reported_total_count = _optional_nonnegative_int(data.get("total_count"))
            record_cap_hit = False

            for record_index, record in enumerate(records):
                if len(jobs) >= self.max_jobs:
                    record_cap_hit = True
                    break
                if not isinstance(record, Mapping):
                    failures.append(
                        self._failure(
                            "record",
                            "posting is not a JSON object",
                            page=page_number,
                            job_id=f"record-{record_index + 1}",
                        )
                    )
                    continue
                native_id = clean_text(record.get("id"))
                try:
                    job = self._to_job(record)
                    if job.job_id in seen_ids:
                        continue
                    seen_ids.add(job.job_id or "")
                    jobs.append(job)
                except Exception as exc:
                    failures.append(
                        self._failure(
                            "record",
                            exc,
                            page=page_number,
                            job_id=native_id or None,
                        )
                    )

            if record_cap_hit:
                truncated = True
                break
            if len(jobs) >= self.max_jobs:
                more_reported = (
                    reported_total_count is None
                    or total_seen < reported_total_count
                    or (
                        reported_total_pages is not None
                        and page_number < reported_total_pages
                    )
                )
                truncated = more_reported
                break
            if not records:
                break
            if reported_total_pages is not None and page_number >= reported_total_pages:
                break
            if reported_total_pages is None and len(records) < self.page_size:
                break
        else:
            truncated = (
                reported_total_pages is None
                or self.max_pages < reported_total_pages
                or (
                    reported_total_count is not None
                    and total_seen < reported_total_count
                )
            )

        return SourceDiscoveryResult(
            source=self.source_name,
            account=self.account,
            jobs=tuple(jobs),
            failures=tuple(failures),
            pages_fetched=pages_fetched,
            total_seen=total_seen,
            truncated=truncated,
        )

    @staticmethod
    def _page_data(payload: Any) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("expected a JSON object")
        if payload.get("code") not in (0, "0"):
            message = clean_text(payload.get("message")) or "unknown API error"
            raise ValueError(f"Li Auto campus API returned an error: {message}")
        data = payload.get("data")
        if not isinstance(data, Mapping) or not isinstance(data.get("items"), list):
            raise ValueError("expected data.items to be an array")
        return data

    def _to_job(self, record: Mapping[str, Any]) -> JobPosting:
        title = require_title(record.get("title"))
        native_id = clean_text(record.get("id"))
        job_code = clean_text(record.get("code"))
        if not native_id:
            raise ValueError("job id is missing")
        if not job_code:
            raise ValueError("job code is missing")

        detail_url = (
            f"{self._DETAIL_BASE}/{quote(native_id, safe='')}.html"
            f"?jobCode={quote(job_code, safe='')}"
        )
        job_url = safe_https_url(detail_url)
        if not job_url:
            raise ValueError("official job URL is invalid")

        job_id = stable_job_id(self.source_name, self.account, native_id)
        return JobPosting(
            id=job_id,
            job_id=job_id,
            title=title,
            company=self.company,
            location=clean_text(record.get("location_title")) or None,
            job_type=clean_text(record.get("job_mode_name")) or None,
            department=join_unique(
                [
                    record.get("department_title"),
                    record.get("first_job_function_title"),
                    record.get("second_job_function_title"),
                ]
            )
            or None,
            description="",
            requirements=None,
            job_url=job_url,
            source=self.source_name,
        )


def _optional_nonnegative_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


LiAutoCampusAdapter = LiAutoCampusSource

__all__ = ["LiAutoCampusAdapter", "LiAutoCampusSource"]
