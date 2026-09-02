from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from ai_job_agent.models import JobPosting
from ai_job_agent.services import official_source_searcher as searcher
from ai_job_agent.sources import LiAutoCampusSource
from ai_job_agent.sources.base import SourceDiscoveryResult


class FakeResponse:
    def __init__(
        self, payload: Any = None, *, status_error: Exception | None = None
    ) -> None:
        self.payload = payload
        self.status_error = status_error

    def raise_for_status(self) -> None:
        if self.status_error:
            raise self.status_error

    def json(self) -> Any:
        return self.payload


class FakeSession:
    def __init__(self, handler: Callable[..., FakeResponse]) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        call = {"url": url, **kwargs}
        self.calls.append(call)
        return self.handler(url=url, **kwargs)


def _payload(
    items: list[Any], *, page: int, total_pages: int, total_count: int
) -> dict[str, Any]:
    return {
        "code": 0,
        "message": "成功",
        "data": {
            "page": page,
            "page_size": len(items),
            "total_pages": total_pages,
            "total_count": total_count,
            "items": items,
        },
    }


def _job(native_id: int, code: str, title: str) -> dict[str, Any]:
    return {
        "id": native_id,
        "code": code,
        "title": title,
        "location_title": "上海闵行区",
        "job_mode_name": "正式",
        "department_title": "智能空间",
        "first_job_function_title": "研发",
        "second_job_function_title": "软件",
    }


def test_liauto_campus_maps_and_paginates_via_public_https_get_only() -> None:
    pages = {
        1: _payload(
            [_job(18432, "A208039", "对外事务专员"), _job(18433, "A208040", "AI 工程师")],
            page=1,
            total_pages=2,
            total_count=3,
        ),
        2: _payload(
            [_job(18434, "A208041", "数据开发工程师")],
            page=2,
            total_pages=2,
            total_count=3,
        ),
    }

    def handler(*, url: str, params: dict[str, Any], **_: Any) -> FakeResponse:
        assert url == (
            "https://www.lixiang.com/"
            "osd-hr-recruitment-website/v1/recruit/school/job-page"
        )
        return FakeResponse(pages[params["page"]])

    session = FakeSession(handler)
    result = LiAutoCampusSource(
        session=session,
        timeout=4.5,
        page_size=2,
        max_pages=3,
        max_jobs=3,
    ).discover()

    assert [job.title for job in result.jobs] == [
        "对外事务专员",
        "AI 工程师",
        "数据开发工程师",
    ]
    assert result.pages_fetched == 2
    assert result.total_seen == 3
    assert not result.truncated
    assert not result.failures
    first = result.jobs[0]
    assert first.job_id == "li auto campus:lixiang:18432"
    assert first.company == "理想汽车"
    assert first.location == "上海闵行区"
    assert first.job_type == "正式"
    assert first.department == "智能空间 / 研发 / 软件"
    assert first.job_url == (
        "https://www.lixiang.com/employ/detail/18432.html?jobCode=A208039"
    )
    assert first.source == "Li Auto Campus"
    assert all(call["url"].startswith("https://www.lixiang.com/") for call in session.calls)
    assert all("login" not in call["url"] and "apply" not in call["url"] for call in session.calls)
    assert all(call["timeout"] == 4.5 for call in session.calls)
    assert session.calls[0]["params"] == {
        "page": 1,
        "page_size": 2,
        "project_id": 25,
    }
    assert session.calls[1]["params"] == {
        "page": 2,
        "page_size": 2,
        "project_id": 25,
    }


def test_liauto_campus_enforces_job_cap_without_extra_page() -> None:
    session = FakeSession(
        lambda **_: FakeResponse(
            _payload(
                [_job(1, "C1", "One"), _job(2, "C2", "Two")],
                page=1,
                total_pages=3,
                total_count=6,
            )
        )
    )

    result = LiAutoCampusSource(
        session=session, page_size=2, max_pages=5, max_jobs=1
    ).discover()

    assert [job.title for job in result.jobs] == ["One"]
    assert result.truncated
    assert result.pages_fetched == 1
    assert len(session.calls) == 1


def test_liauto_campus_isolates_bad_record_and_later_page_failure() -> None:
    def handler(*, params: dict[str, Any], **_: Any) -> FakeResponse:
        if params["page"] == 1:
            return FakeResponse(
                _payload(
                    [
                        {"id": 99, "code": "BROKEN", "title": ""},
                        _job(100, "OK100", "平台开发工程师"),
                    ],
                    page=1,
                    total_pages=2,
                    total_count=3,
                )
            )
        return FakeResponse(status_error=RuntimeError("temporary 503"))

    result = LiAutoCampusSource(
        session=FakeSession(handler), page_size=2, max_pages=3
    ).discover()

    assert [job.title for job in result.jobs] == ["平台开发工程师"]
    assert result.pages_fetched == 1
    assert [failure.stage for failure in result.failures] == ["record", "list"]
    assert result.failures[0].job_id == "99"
    assert result.failures[1].page == 2
    assert "temporary 503" in result.failures[1].message


@pytest.mark.parametrize(
    "url",
    [
        "https://www.lixiang.com/employ/campus/list.html?fromJob=1",
        (
            "https://www.lixiang.com/osd-hr-recruitment-website/"
            "v1/recruit/school/job-page"
        ),
    ],
)
def test_detects_only_liauto_official_campus_routes(url: str) -> None:
    detected = searcher.detect_career_source(url)
    assert (detected.kind, detected.account) == ("liauto-campus", "lixiang")

    unrelated = searcher.detect_career_source("https://www.lixiang.com/car")
    assert unrelated.kind == "generic"


def test_official_searcher_routes_liauto_to_read_only_adapter(monkeypatch) -> None:
    job = JobPosting(
        job_id="li auto campus:lixiang:18432",
        title="AI 工程师",
        company="理想汽车",
        job_url="https://www.lixiang.com/employ/detail/18432.html?jobCode=A208039",
        source="Li Auto Campus",
    )
    captured: dict[str, Any] = {}

    class StubLiAutoCampusSource:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def discover(self) -> SourceDiscoveryResult:
            return SourceDiscoveryResult(
                source="Li Auto Campus",
                account="lixiang",
                jobs=(job,),
                pages_fetched=1,
                total_seen=1,
            )

    monkeypatch.setattr(searcher, "LiAutoCampusSource", StubLiAutoCampusSource)
    result = searcher.search_official_career_urls(
        ["https://www.lixiang.com/employ/campus/list.html?fromJob=1"],
        company="理想汽车",
        max_jobs_per_source=27,
    )

    assert result.jobs == (job,)
    assert result.sources_succeeded == ("liauto-campus:lixiang",)
    assert captured == {"company": "理想汽车", "max_jobs": 27, "max_pages": 5}
