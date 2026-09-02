"""Read-only adapter for Lever's public Postings API (v0)."""

from __future__ import annotations

from typing import Any, Mapping

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


class LeverSource(JobSourceAdapter):
    """Fetch published jobs from a single global or EU Lever site."""

    source_name = "Lever"

    def __init__(self, site: str, *, eu: bool = False, **kwargs: Any) -> None:
        super().__init__(site, **kwargs)
        self.eu = bool(eu)
        api_host = "api.eu.lever.co" if self.eu else "api.lever.co"
        jobs_host = "jobs.eu.lever.co" if self.eu else "jobs.lever.co"
        account = quoted_account(self.account)
        self.endpoint = f"https://{api_host}/v0/postings/{account}"
        self.board_url = f"https://{jobs_host}/{account}"

    def discover(self) -> SourceDiscoveryResult:
        jobs: list[JobPosting] = []
        failures = []
        seen_ids: set[str] = set()
        total_seen = 0
        pages_fetched = 0
        offset = 0
        truncated = False

        for page_index in range(self.max_pages):
            if len(jobs) >= self.max_jobs:
                truncated = True
                break
            request_limit = min(self.page_size, self.max_jobs - len(jobs))
            try:
                payload = self._get_json(
                    self.endpoint,
                    params={"mode": "json", "skip": offset, "limit": request_limit},
                )
            except Exception as exc:
                failures.append(self._failure("list", exc, page=page_index + 1))
                break
            if not isinstance(payload, list):
                failures.append(
                    self._failure(
                        "list",
                        "expected a JSON array of Lever postings",
                        page=page_index + 1,
                    )
                )
                break

            pages_fetched += 1
            total_seen += len(payload)
            for record_index, record in enumerate(payload):
                if len(jobs) >= self.max_jobs:
                    truncated = True
                    break
                try:
                    if not isinstance(record, Mapping):
                        raise ValueError("posting is not a JSON object")
                    job = self._to_job(record)
                    if job.job_id in seen_ids:
                        continue
                    seen_ids.add(job.job_id or "")
                    jobs.append(job)
                except Exception as exc:
                    native_id = (
                        clean_text(record.get("id"))
                        if isinstance(record, Mapping)
                        else f"record-{record_index + 1}"
                    )
                    failures.append(
                        self._failure(
                            "record",
                            exc,
                            page=page_index + 1,
                            job_id=native_id or None,
                        )
                    )

            offset += len(payload)
            if len(payload) < request_limit:
                break
            if len(jobs) >= self.max_jobs:
                truncated = True
                break
        else:
            truncated = True

        return SourceDiscoveryResult(
            source=self.source_name,
            account=self.account,
            jobs=tuple(jobs),
            failures=tuple(failures),
            pages_fetched=pages_fetched,
            total_seen=total_seen,
            truncated=truncated,
        )

    def _to_job(self, record: Mapping[str, Any]) -> JobPosting:
        title = require_title(record.get("text"))
        categories = record.get("categories")
        categories = categories if isinstance(categories, Mapping) else {}

        locations: list[Any] = [categories.get("location")]
        all_locations = categories.get("allLocations")
        if isinstance(all_locations, list):
            locations.extend(all_locations)
        location = join_unique(locations) or None

        lists = record.get("lists")
        requirement_parts: list[str] = []
        if isinstance(lists, list):
            for section in lists:
                if not isinstance(section, Mapping):
                    continue
                heading = clean_text(section.get("text"))
                content = clean_html(section.get("content"))
                heading_key = heading.casefold()
                if content and any(
                    keyword in heading_key
                    for keyword in (
                        "require",
                        "qualif",
                        "skill",
                        "what you",
                        "experience",
                        "you have",
                    )
                ):
                    requirement_parts.append(
                        f"{heading}\n{content}" if heading else content
                    )

        description = clean_text(record.get("descriptionPlain"))
        if not description:
            description = clean_html(record.get("description"))
        if not description:
            description = join_unique(
                [record.get("openingPlain"), record.get("additionalPlain")],
                separator="\n",
            )

        native_id = record.get("id")
        fallback_url = self.board_url
        if clean_text(native_id):
            fallback_url = f"{self.board_url}/{quoted_account(str(native_id))}"
        hosted_url = safe_https_url(record.get("hostedUrl"))
        apply_url = safe_https_url(record.get("applyUrl"))
        job_url = hosted_url or apply_url or fallback_url
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
            location=location,
            job_type=clean_text(categories.get("commitment")) or None,
            department=join_unique(
                [categories.get("department"), categories.get("team")]
            )
            or None,
            description=description,
            requirements="\n\n".join(requirement_parts) or None,
            job_url=job_url,
            source=self.source_name,
            publish_date=None,
        )


LeverAdapter = LeverSource

__all__ = ["LeverAdapter", "LeverSource"]
