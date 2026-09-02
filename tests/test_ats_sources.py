from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from ai_job_agent.sources import (
    AshbySource,
    LeverSource,
    SmartRecruitersSource,
    clean_html,
)


class FakeResponse:
    def __init__(
        self,
        payload: Any = None,
        *,
        status_error: Exception | None = None,
        json_error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.status_error = status_error
        self.json_error = json_error

    def raise_for_status(self) -> None:
        if self.status_error:
            raise self.status_error

    def json(self) -> Any:
        if self.json_error:
            raise self.json_error
        return self.payload


class FakeSession:
    def __init__(self, handler: Callable[..., FakeResponse]) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        call = {"url": url, **kwargs}
        self.calls.append(call)
        return self.handler(url=url, **kwargs)


def test_clean_html_removes_active_markup_and_keeps_readable_structure() -> None:
    result = clean_html(
        "<h2>Role &amp; scope</h2><p>Build <b>safe</b> agents<br>in Python.</p>"
        "<script>alert('no')</script><style>p{display:none}</style>"
    )

    assert result == "Role & scope\nBuild safe agents\nin Python."
    assert "alert" not in result


def test_source_configuration_is_bounded_and_rejects_path_injection() -> None:
    session = FakeSession(lambda **_: FakeResponse([]))
    source = LeverSource(
        "example",
        session=session,
        timeout=3,
        page_size=999,
        max_pages=2,
        max_jobs=8,
    )

    assert source.page_size == 100
    with pytest.raises(ValueError):
        LeverSource("../other", session=session)
    with pytest.raises(ValueError):
        AshbySource("board?includeHidden=true", session=session)


def test_lever_maps_public_postings_and_paginates_with_injected_session() -> None:
    pages = {
        0: [
            {
                "id": "abc-1",
                "text": "AI Engineer",
                "categories": {
                    "location": "Shanghai",
                    "allLocations": ["Shanghai", "Remote - China"],
                    "commitment": "Full-time",
                    "department": "Engineering",
                    "team": "AI",
                },
                "description": "<p>Build <b>LLM</b> workflows.</p>",
                "lists": [
                    {
                        "text": "Requirements",
                        "content": "<ul><li>Python</li><li>SQL</li></ul>",
                    }
                ],
                "hostedUrl": "https://jobs.lever.co/acme/abc-1",
                "applyUrl": "https://jobs.lever.co/acme/abc-1/apply",
            },
            {
                "id": "abc-2",
                "text": "Data Analyst",
                "categories": {"location": "Beijing", "commitment": "Intern"},
                "descriptionPlain": "Analyze product data.",
                "hostedUrl": "https://jobs.lever.co/acme/abc-2",
            },
        ],
        2: [],
    }

    def handler(*, url: str, params: dict[str, Any], **_: Any) -> FakeResponse:
        assert url == "https://api.lever.co/v0/postings/acme"
        return FakeResponse(pages[params["skip"]])

    session = FakeSession(handler)
    result = LeverSource(
        "acme",
        company="Acme China",
        session=session,
        timeout=4.5,
        page_size=2,
        max_pages=3,
    ).discover()

    assert len(result.jobs) == 2
    assert result.pages_fetched == 2
    assert result.total_seen == 2
    assert not result.failures
    first = result.jobs[0]
    assert first.job_id == "lever:acme:abc-1"
    assert first.id == first.job_id
    assert first.company == "Acme China"
    assert first.location == "Shanghai / Remote - China"
    assert first.job_type == "Full-time"
    assert first.department == "Engineering / AI"
    assert first.description == "Build LLM workflows."
    assert first.requirements == "Requirements\nPython\nSQL"
    assert first.job_url == "https://jobs.lever.co/acme/abc-1"
    assert first.source == "Lever"
    assert session.calls[0]["timeout"] == 4.5
    assert session.calls[0]["headers"]["Accept"] == "application/json"
    assert session.calls[0]["params"] == {"mode": "json", "skip": 0, "limit": 2}


def test_lever_keeps_first_page_when_later_page_fails() -> None:
    def handler(*, params: dict[str, Any], **_: Any) -> FakeResponse:
        if params["skip"] == 0:
            return FakeResponse(
                [
                    {
                        "id": "one",
                        "text": "Engineer",
                        "hostedUrl": "https://jobs.lever.co/demo/one",
                    }
                ]
            )
        return FakeResponse(status_error=RuntimeError("temporary 503"))

    result = LeverSource(
        "demo",
        session=FakeSession(handler),
        page_size=1,
        max_pages=3,
    ).discover()

    assert [job.title for job in result.jobs] == ["Engineer"]
    assert result.pages_fetched == 1
    assert len(result.failures) == 1
    assert result.failures[0].stage == "list"
    assert result.failures[0].page == 2
    assert "temporary 503" in result.failures[0].message


def test_lever_bad_record_is_isolated_and_id_fallback_is_stable() -> None:
    payload = [
        {"id": "broken", "text": ""},
        {
            "text": "Platform Intern",
            "categories": {"location": "Shenzhen"},
            "descriptionPlain": "Build platforms",
        },
    ]
    session = FakeSession(lambda **_: FakeResponse(payload))

    first = LeverSource(
        "demo", session=session, page_size=10, max_pages=1
    ).discover()
    second = LeverSource(
        "demo",
        session=FakeSession(lambda **_: FakeResponse(payload)),
        page_size=10,
        max_pages=1,
    ).discover()

    assert len(first.jobs) == 1
    assert len(first.failures) == 1
    assert first.failures[0].job_id == "broken"
    assert first.jobs[0].job_id == second.jobs[0].job_id
    assert first.jobs[0].job_url == "https://jobs.lever.co/demo"


def test_ashby_maps_listed_jobs_and_hides_unlisted_direct_links() -> None:
    payload = {
        "apiVersion": "1",
        "jobs": [
            {
                "title": "Agent Engineer",
                "location": "Shanghai",
                "secondaryLocations": [{"location": "Beijing"}],
                "department": "R&D",
                "team": "Agents",
                "isRemote": True,
                "workplaceType": "Hybrid",
                "descriptionHtml": "<p>Ship <em>reliable</em> agents.</p>",
                "publishedAt": "2026-08-30T08:00:00Z",
                "employmentType": "FullTime",
                "jobUrl": "https://jobs.ashbyhq.com/acme/8a3f-job",
                "applyUrl": "https://jobs.ashbyhq.com/acme/8a3f-job/application",
                "isListed": True,
            },
            {
                "title": "Secret role",
                "jobUrl": "https://jobs.ashbyhq.com/acme/secret",
                "isListed": False,
            },
        ],
    }
    session = FakeSession(lambda **_: FakeResponse(payload))

    result = AshbySource("acme", company="Acme", session=session).discover()

    assert len(result.jobs) == 1
    assert result.total_seen == 2
    assert result.pages_fetched == 1
    job = result.jobs[0]
    assert job.job_id == "ashby:acme:8a3f-job"
    assert job.location == "Shanghai / Beijing / Remote"
    assert job.department == "R&D / Agents"
    assert job.job_type == "FullTime"
    assert job.description == "Ship reliable agents."
    assert job.publish_date == "2026-08-30T08:00:00Z"
    assert session.calls[0]["params"] == {"includeCompensation": "true"}


def test_ashby_enforces_job_cap_and_isolates_invalid_records() -> None:
    payload = {
        "jobs": [
            {"title": "", "isListed": True},
            {
                "title": "One",
                "jobUrl": "https://jobs.ashbyhq.com/demo/one",
                "isListed": True,
            },
            {
                "title": "Two",
                "jobUrl": "https://jobs.ashbyhq.com/demo/two",
                "isListed": True,
            },
        ]
    }
    result = AshbySource(
        "demo", session=FakeSession(lambda **_: FakeResponse(payload)), max_jobs=1
    ).discover()

    assert [job.title for job in result.jobs] == ["One"]
    assert result.truncated
    assert len(result.failures) == 1
    assert result.failures[0].stage == "record"


def test_smartrecruiters_paginates_and_enriches_summaries_with_details() -> None:
    summaries = {
        0: {
            "limit": 1,
            "offset": 0,
            "totalFound": 2,
            "content": [
                {
                    "id": "101",
                    "name": "Machine Learning Engineer",
                    "company": {"identifier": "acme", "name": "Acme"},
                    "location": {"city": "Shanghai", "country": "CN"},
                    "releasedDate": "2026-08-20T00:00:00Z",
                }
            ],
        },
        1: {
            "limit": 1,
            "offset": 1,
            "totalFound": 2,
            "content": [
                {
                    "id": "102",
                    "name": "Data Intern",
                    "company": {"identifier": "acme", "name": "Acme"},
                    "location": {"city": "Beijing", "country": "CN"},
                }
            ],
        },
    }
    details = {
        "101": {
            "id": "101",
            "name": "Machine Learning Engineer",
            "company": {"identifier": "acme", "name": "Acme China"},
            "location": {
                "city": "Shanghai",
                "region": "Shanghai",
                "country": "CN",
                "remote": True,
            },
            "department": {"label": "Engineering"},
            "function": {"label": "AI"},
            "typeOfEmployment": {"label": "Full-time"},
            "applyUrl": "https://jobs.smartrecruiters.com/Acme/101-ml-engineer",
            "releasedDate": "2026-08-20T00:00:00Z",
            "jobAd": {
                "sections": {
                    "jobDescription": {
                        "title": "Job Description",
                        "text": "<p>Build <b>models</b>.</p>",
                    },
                    "qualifications": {
                        "title": "Qualifications",
                        "text": "<ul><li>Python</li><li>PyTorch</li></ul>",
                    },
                }
            },
        },
        "102": {
            "id": "102",
            "name": "Data Intern",
            "company": {"name": "Acme China"},
            "applyUrl": "https://jobs.smartrecruiters.com/Acme/102-data-intern",
        },
    }

    def handler(*, url: str, params: dict[str, Any], **_: Any) -> FakeResponse:
        if url.endswith("/postings"):
            assert params["destination"] == "PUBLIC"
            return FakeResponse(summaries[params["offset"]])
        return FakeResponse(details[url.rsplit("/", 1)[-1]])

    session = FakeSession(handler)
    result = SmartRecruitersSource(
        "acme", session=session, page_size=1, max_pages=3
    ).discover()

    assert len(result.jobs) == 2
    assert result.pages_fetched == 2
    assert result.total_seen == 2
    assert not result.truncated
    first = result.jobs[0]
    assert first.job_id == "smartrecruiters:acme:101"
    assert first.company == "Acme China"
    assert first.location == "Shanghai, CN / Remote"
    assert first.department == "Engineering / AI"
    assert first.job_type == "Full-time"
    assert first.description == (
        "Job Description\nBuild models.\n\nQualifications\nPython\nPyTorch"
    )
    assert first.requirements == "Python\nPyTorch"
    assert first.job_url == "https://jobs.smartrecruiters.com/Acme/101-ml-engineer"
    assert first.publish_date == "2026-08-20T00:00:00Z"


def test_smartrecruiters_detail_failure_keeps_summary_job() -> None:
    summary = {
        "totalFound": 1,
        "content": [
            {
                "id": "404",
                "name": "Business Analyst",
                "company": {"name": "Acme"},
                "location": {"city": "Shenzhen", "country": "CN"},
                "typeOfEmployment": {"label": "Intern"},
            }
        ],
    }

    def handler(*, url: str, **_: Any) -> FakeResponse:
        if url.endswith("/postings"):
            return FakeResponse(summary)
        return FakeResponse(status_error=RuntimeError("detail unavailable"))

    result = SmartRecruitersSource(
        "acme", session=FakeSession(handler), page_size=100
    ).discover()

    assert len(result.jobs) == 1
    assert result.jobs[0].title == "Business Analyst"
    assert result.jobs[0].job_url == "https://careers.smartrecruiters.com/acme"
    assert len(result.failures) == 1
    assert result.failures[0].stage == "detail"
    assert result.failures[0].job_id == "404"


def test_smartrecruiters_can_skip_detail_calls_and_caps_pagination() -> None:
    page = {
        "totalFound": 9,
        "content": [
            {
                "id": "1",
                "name": "First",
                "applyUrl": "https://jobs.smartrecruiters.com/Demo/1-first",
            },
            {
                "id": "2",
                "name": "Second",
                "applyUrl": "https://jobs.smartrecruiters.com/Demo/2-second",
            },
        ],
    }
    session = FakeSession(lambda **_: FakeResponse(page))
    result = SmartRecruitersSource(
        "demo",
        session=session,
        include_details=False,
        page_size=2,
        max_pages=1,
        max_jobs=2,
    ).discover()

    assert [job.title for job in result.jobs] == ["First", "Second"]
    assert result.truncated
    assert len(session.calls) == 1
