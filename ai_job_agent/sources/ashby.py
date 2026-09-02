"""Read-only adapter for Ashby's public Job Postings API."""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlsplit

from ai_job_agent.models import JobPosting

from .base import (
    JobSourceAdapter,
    SourceDiscoveryResult,
    clean_html,
    clean_text,
    join_unique,
    quoted_account,
    require_title,
    safe_https_url,
    stable_job_id,
)


class AshbySource(JobSourceAdapter):
    """Fetch listed jobs from one Ashby-hosted job board."""

    source_name = "Ashby"

    def __init__(self, board: str, **kwargs: Any) -> None:
        super().__init__(board, **kwargs)
        account = quoted_account(self.account)
        self.endpoint = f"https://api.ashbyhq.com/posting-api/job-board/{account}"
        self.board_url = f"https://jobs.ashbyhq.com/{account}"

    def discover(self) -> SourceDiscoveryResult:
        failures = []
        try:
            payload = self._get_json(
                self.endpoint, params={"includeCompensation": "true"}
            )
        except Exception as exc:
            return SourceDiscoveryResult(
                source=self.source_name,
                account=self.account,
                jobs=(),
                failures=(self._failure("list", exc, page=1),),
            )
        if not isinstance(payload, Mapping) or not isinstance(payload.get("jobs"), list):
            return SourceDiscoveryResult(
                source=self.source_name,
                account=self.account,
                jobs=(),
                failures=(
                    self._failure("list", "expected an object containing a jobs array", page=1),
                ),
                pages_fetched=1,
            )

        records = payload["jobs"]
        jobs: list[JobPosting] = []
        seen_ids: set[str] = set()
        listed_seen = 0
        truncated = False
        for record_index, record in enumerate(records):
            if not isinstance(record, Mapping):
                failures.append(
                    self._failure(
                        "record",
                        "posting is not a JSON object",
                        page=1,
                        job_id=f"record-{record_index + 1}",
                    )
                )
                continue
            if record.get("isListed") is False:
                continue
            listed_seen += 1
            if len(jobs) >= self.max_jobs:
                truncated = True
                continue
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
                        page=1,
                        job_id=self._native_id(record) or None,
                    )
                )

        return SourceDiscoveryResult(
            source=self.source_name,
            account=self.account,
            jobs=tuple(jobs),
            failures=tuple(failures),
            pages_fetched=1,
            total_seen=len(records),
            truncated=truncated or listed_seen > self.max_jobs,
        )

    def _native_id(self, record: Mapping[str, Any]) -> str:
        explicit = clean_text(record.get("id"))
        if explicit:
            return explicit
        for key in ("jobUrl", "applyUrl"):
            url = safe_https_url(record.get(key))
            if not url:
                continue
            path_parts = [part for part in urlsplit(url).path.split("/") if part]
            if path_parts:
                candidate = path_parts[-1]
                if candidate.casefold() != "apply":
                    return candidate
                if len(path_parts) > 1:
                    return path_parts[-2]
        return ""

    def _to_job(self, record: Mapping[str, Any]) -> JobPosting:
        title = require_title(record.get("title"))
        primary_location = clean_text(record.get("location"))
        locations: list[Any] = [primary_location]
        secondary = record.get("secondaryLocations")
        if isinstance(secondary, list):
            for location in secondary:
                if isinstance(location, Mapping):
                    locations.append(location.get("location"))
        location = join_unique(locations)
        if record.get("isRemote") and "remote" not in location.casefold():
            location = join_unique([location, "Remote"])

        description = clean_text(record.get("descriptionPlain"))
        if not description:
            description = clean_html(record.get("descriptionHtml"))

        native_id = self._native_id(record)
        job_url = (
            safe_https_url(record.get("jobUrl"))
            or safe_https_url(record.get("applyUrl"))
            or self.board_url
        )
        job_id = stable_job_id(
            self.source_name,
            self.account,
            native_id,
            fingerprint=(title, location, job_url),
        )
        return JobPosting(
            id=job_id,
            job_id=job_id,
            title=title,
            company=self.company,
            location=location or None,
            job_type=clean_text(record.get("employmentType")) or None,
            department=join_unique([record.get("department"), record.get("team")])
            or None,
            description=description,
            requirements=None,
            job_url=job_url,
            source=self.source_name,
            publish_date=clean_text(record.get("publishedAt")) or None,
        )


AshbyAdapter = AshbySource

__all__ = ["AshbyAdapter", "AshbySource"]
