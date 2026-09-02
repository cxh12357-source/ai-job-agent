"""Read-only adapter for the SmartRecruiters public Posting API."""

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


class SmartRecruitersSource(JobSourceAdapter):
    """Fetch public postings, enriching each summary with its public detail."""

    source_name = "SmartRecruiters"

    def __init__(
        self,
        company_identifier: str,
        *,
        include_details: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(company_identifier, **kwargs)
        account = quoted_account(self.account)
        self.endpoint = (
            f"https://api.smartrecruiters.com/v1/companies/{account}/postings"
        )
        self.board_url = f"https://careers.smartrecruiters.com/{account}"
        self.include_details = bool(include_details)

    def discover(self) -> SourceDiscoveryResult:
        jobs: list[JobPosting] = []
        failures = []
        seen_ids: set[str] = set()
        pages_fetched = 0
        total_seen = 0
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
                    params={
                        "limit": request_limit,
                        "offset": offset,
                        "destination": "PUBLIC",
                    },
                )
            except Exception as exc:
                failures.append(self._failure("list", exc, page=page_index + 1))
                break
            if not isinstance(payload, Mapping) or not isinstance(
                payload.get("content"), list
            ):
                failures.append(
                    self._failure(
                        "list",
                        "expected an object containing a content array",
                        page=page_index + 1,
                    )
                )
                break

            records = payload["content"]
            pages_fetched += 1
            total_seen += len(records)
            for record_index, summary in enumerate(records):
                if len(jobs) >= self.max_jobs:
                    truncated = True
                    break
                if not isinstance(summary, Mapping):
                    failures.append(
                        self._failure(
                            "record",
                            "posting is not a JSON object",
                            page=page_index + 1,
                            job_id=f"record-{record_index + 1}",
                        )
                    )
                    continue

                native_id = self._native_id(summary)
                detail: Mapping[str, Any] = {}
                if self.include_details and native_id:
                    try:
                        detail_payload = self._get_json(
                            f"{self.endpoint}/{quoted_account(native_id)}"
                        )
                        if not isinstance(detail_payload, Mapping):
                            raise ValueError("posting detail is not a JSON object")
                        detail = detail_payload
                    except Exception as exc:
                        failures.append(
                            self._failure(
                                "detail",
                                exc,
                                page=page_index + 1,
                                job_id=native_id,
                            )
                        )

                record = _prefer_detail(summary, detail)
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
                            page=page_index + 1,
                            job_id=native_id or None,
                        )
                    )

            offset += len(records)
            total_found = _optional_nonnegative_int(payload.get("totalFound"))
            if len(records) < request_limit:
                break
            if total_found is not None and offset >= total_found:
                break
            if len(jobs) >= self.max_jobs:
                truncated = total_found is None or offset < total_found
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

    @staticmethod
    def _native_id(record: Mapping[str, Any]) -> str:
        return clean_text(
            record.get("id") or record.get("uuid") or record.get("jobAdId")
        )

    def _to_job(self, record: Mapping[str, Any]) -> JobPosting:
        title = require_title(record.get("name"))
        company_record = record.get("company")
        company_record = company_record if isinstance(company_record, Mapping) else {}
        company = clean_text(company_record.get("name")) or self.company

        location_record = record.get("location")
        location_record = (
            location_record if isinstance(location_record, Mapping) else {}
        )
        location = join_unique(
            [
                location_record.get("city"),
                location_record.get("region"),
                location_record.get("country"),
            ],
            separator=", ",
        )
        if location_record.get("remote") and "remote" not in location.casefold():
            location = join_unique([location, "Remote"])

        sections = _job_ad_sections(record)
        description_parts: list[str] = []
        requirements = ""
        for key, section in sections.items():
            if not isinstance(section, Mapping):
                continue
            heading = clean_text(section.get("title"))
            body = clean_html(section.get("text"))
            if not body:
                continue
            description_parts.append(f"{heading}\n{body}" if heading else body)
            if key.casefold() in {"qualifications", "requirements"} or any(
                word in heading.casefold() for word in ("qualification", "requirement")
            ):
                requirements = body

        description = "\n\n".join(description_parts)
        if not description:
            description = clean_html(record.get("description"))

        department_record = record.get("department")
        department_record = (
            department_record if isinstance(department_record, Mapping) else {}
        )
        function_record = record.get("function")
        function_record = function_record if isinstance(function_record, Mapping) else {}
        employment_record = record.get("typeOfEmployment")
        employment_record = (
            employment_record if isinstance(employment_record, Mapping) else {}
        )

        native_id = self._native_id(record)
        job_url = (
            safe_https_url(record.get("applyUrl"))
            or safe_https_url(record.get("jobUrl"))
            or self.board_url
        )
        job_id = stable_job_id(
            self.source_name,
            self.account,
            native_id,
            fingerprint=(title, company, location, job_url),
        )
        return JobPosting(
            id=job_id,
            job_id=job_id,
            title=title,
            company=company,
            location=location or None,
            job_type=clean_text(employment_record.get("label")) or None,
            department=join_unique(
                [department_record.get("label"), function_record.get("label")]
            )
            or None,
            description=description,
            requirements=requirements or None,
            job_url=job_url,
            source=self.source_name,
            publish_date=clean_text(record.get("releasedDate")) or None,
        )


def _prefer_detail(
    summary: Mapping[str, Any], detail: Mapping[str, Any]
) -> dict[str, Any]:
    result = dict(summary)
    for key, value in detail.items():
        if value is not None:
            result[key] = value
    return result


def _job_ad_sections(record: Mapping[str, Any]) -> Mapping[str, Any]:
    job_ad = record.get("jobAd")
    if not isinstance(job_ad, Mapping):
        return {}
    sections = job_ad.get("sections")
    return sections if isinstance(sections, Mapping) else {}


def _optional_nonnegative_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


SmartRecruitersAdapter = SmartRecruitersSource

__all__ = ["SmartRecruitersAdapter", "SmartRecruitersSource"]
