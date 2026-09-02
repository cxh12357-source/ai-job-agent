from __future__ import annotations

from threading import Event, Lock
from types import SimpleNamespace

import pytest

from ai_job_agent.models import JobPosting
from ai_job_agent.services import official_source_searcher as searcher
from ai_job_agent.sources.campus_catalog import MAX_CAMPUS_SOURCES_PER_SEARCH


@pytest.mark.parametrize(
    ("url", "kind", "account", "eu"),
    [
        ("https://jobs.lever.co/acme/role-1", "lever", "acme", False),
        ("https://jobs.eu.lever.co/acme", "lever", "acme", True),
        ("https://api.lever.co/v0/postings/acme", "lever", "acme", False),
        ("https://jobs.ashbyhq.com/Acme/role-1", "ashby", "Acme", False),
        (
            "https://api.ashbyhq.com/posting-api/job-board/Acme",
            "ashby",
            "Acme",
            False,
        ),
        (
            "https://careers.smartrecruiters.com/Acme/role-1",
            "smartrecruiters",
            "Acme",
            False,
        ),
        (
            "https://jobs.smartrecruiters.com/Acme/role-1",
            "smartrecruiters",
            "Acme",
            False,
        ),
        (
            "https://api.smartrecruiters.com/v1/companies/Acme/postings",
            "smartrecruiters",
            "Acme",
            False,
        ),
        ("https://careers.example.com/jobs", "generic", None, False),
    ],
)
def test_detects_public_ats_from_board_url(
    url: str, kind: str, account: str | None, eu: bool
) -> None:
    detected = searcher.detect_career_source(url)
    assert (detected.kind, detected.account, detected.eu) == (kind, account, eu)


@pytest.mark.parametrize(
    "url",
    ("not-a-url", "javascript:alert(1)", "https://user:secret@example.com/jobs"),
)
def test_rejects_invalid_source_url(url: str) -> None:
    with pytest.raises(searcher.OfficialCareerSearchError):
        searcher.detect_career_source(url)


def test_multi_source_search_isolates_failures_and_deduplicates(monkeypatch) -> None:
    job = JobPosting(
        job_id="one",
        title="Engineer",
        company="Acme",
        job_url="https://careers.example.com/jobs/one",
        source="Official website:careers.example.com",
    )

    def fake_search(url: str, **_kwargs: object) -> searcher.OfficialCareerSearchResult:
        if "broken" in url:
            raise searcher.OfficialCareerSearchError("temporarily unavailable")
        return searcher.OfficialCareerSearchResult(
            jobs=(job,),
            sources_queried=(url,),
            sources_succeeded=(url,),
            failures=(),
            warnings=(),
            pages_fetched=1,
            total_jobs_seen=1,
        )

    monkeypatch.setattr(searcher, "_search_one", fake_search)
    result = searcher.search_official_career_urls(
        [
            "https://careers.example.com/jobs",
            "https://broken.example.com/jobs",
            "https://careers.example.com/jobs-duplicate",
        ]
    )

    assert result.jobs == (job,)
    assert len(result.sources_succeeded) == 2
    assert len(result.failures) == 1
    assert result.pages_fetched == 2


def test_reviewed_catalog_company_overrides_generic_page_brand(monkeypatch) -> None:
    url = "https://campus.example.com/jobs"
    parsed_job = JobPosting(
        job_id="one",
        title="算法工程师",
        company="Generic Site Brand",
        job_url="https://campus.example.com/jobs/one",
        source="Official website:campus.example.com",
    )

    def fake_search(
        source_url: str, *, company: str | None, max_jobs: int
    ) -> searcher.OfficialCareerSearchResult:
        assert source_url == url
        assert company == "阿里巴巴"
        assert max_jobs == 50
        return searcher.OfficialCareerSearchResult(
            jobs=(parsed_job,),
            sources_queried=(source_url,),
            sources_succeeded=(source_url,),
            failures=(),
            warnings=(),
            pages_fetched=1,
            total_jobs_seen=1,
        )

    monkeypatch.setattr(searcher, "_search_one", fake_search)
    result = searcher.search_official_career_urls(
        [url],
        company_by_url={url: "阿里巴巴"},
        max_jobs_per_source=50,
    )

    assert result.jobs[0].company == "阿里巴巴"
    assert parsed_job.company == "Generic Site Brand"


def test_search_accepts_sources_up_to_catalog_cap(monkeypatch) -> None:
    urls = [
        f"https://careers{i}.example.com/jobs"
        for i in range(MAX_CAMPUS_SOURCES_PER_SEARCH)
    ]

    def fake_search(url: str, **_kwargs: object) -> searcher.OfficialCareerSearchResult:
        return searcher.OfficialCareerSearchResult(
            jobs=(),
            sources_queried=(url,),
            sources_succeeded=(url,),
            failures=(),
            warnings=(),
            pages_fetched=1,
            total_jobs_seen=0,
        )

    monkeypatch.setattr(searcher, "_search_one", fake_search)
    result = searcher.search_official_career_urls(urls)

    assert MAX_CAMPUS_SOURCES_PER_SEARCH > 8
    assert result.sources_queried == tuple(urls)
    assert result.pages_fetched == MAX_CAMPUS_SOURCES_PER_SEARCH


def test_search_rejects_more_than_catalog_cap() -> None:
    urls = [
        f"https://careers{i}.example.com/jobs"
        for i in range(MAX_CAMPUS_SOURCES_PER_SEARCH + 1)
    ]
    with pytest.raises(
        searcher.OfficialCareerSearchError,
        match=str(MAX_CAMPUS_SOURCES_PER_SEARCH),
    ):
        searcher.search_official_career_urls(urls)


def test_cloud_mode_never_starts_rendered_browser_fallback(monkeypatch) -> None:
    class EmptyGenericSource:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def discover(self, _url: str) -> SimpleNamespace:
            return SimpleNamespace(jobs=(), warnings=(), pages_visited=("one",))

    def unexpected_render(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("cloud mode must not launch Playwright")

    monkeypatch.setenv("CLOUD_DEPLOYMENT", "true")
    monkeypatch.setattr(searcher, "GenericOfficialSource", EmptyGenericSource)
    monkeypatch.setattr(searcher, "discover_rendered_jobs", unexpected_render)

    result = searcher._search_one(
        "https://careers.example.com/jobs",
        company="Example",
        max_jobs=10,
    )

    assert result.jobs == ()
    assert any("云端安全版" in warning for warning in result.warnings)


def test_multi_source_search_overlaps_work_and_merges_in_selection_order(
    monkeypatch,
) -> None:
    urls = [
        "https://first.example.com/jobs",
        "https://second.example.com/jobs",
    ]
    lock = Lock()
    overlap_detected = Event()
    first_can_finish = Event()
    active = 0
    peak_active = 0

    def fake_search(url: str, **_kwargs: object) -> searcher.OfficialCareerSearchResult:
        nonlocal active, peak_active
        with lock:
            active += 1
            peak_active = max(peak_active, active)
            if active >= 2:
                overlap_detected.set()
        try:
            if url == urls[0]:
                assert first_can_finish.wait(timeout=5)
            else:
                assert overlap_detected.wait(timeout=5)
                first_can_finish.set()
            position = urls.index(url)
            job = JobPosting(
                job_id=str(position),
                title=f"Engineer {position}",
                company="Acme",
                job_url=f"{url}/{position}",
                source=f"Official website:{position}",
            )
            return searcher.OfficialCareerSearchResult(
                jobs=(job,),
                sources_queried=(url,),
                sources_succeeded=(url,),
                failures=(),
                warnings=(),
                pages_fetched=position + 1,
                total_jobs_seen=position + 1,
            )
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(searcher, "_search_one", fake_search)
    result = searcher.search_official_career_urls(urls)

    assert peak_active >= 2
    assert result.sources_queried == tuple(urls)
    assert tuple(job.job_id for job in result.jobs) == ("0", "1")
    assert result.pages_fetched == 3
    assert result.total_jobs_seen == 3
