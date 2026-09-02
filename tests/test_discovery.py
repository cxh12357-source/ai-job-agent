from __future__ import annotations

from pathlib import Path

import pytest

from job_assistant.discovery import (
    BoardSpec,
    CHINA_APAC_BOARD_CATALOG,
    DEFAULT_BOARD_CATALOG,
    DiscoveryError,
    DiscoveryQuery,
    GreenhouseDiscovery,
    UnknownBoardError,
)
from job_assistant.models import Job


def _job(
    job_id: str,
    title: str,
    location: str,
    description: str,
    *,
    token: str = "alpha",
) -> Job:
    return Job(
        id=job_id,
        title=title,
        company=token.title(),
        location=location,
        description=description,
        url=f"https://job-boards.greenhouse.io/{token}/jobs/{job_id}",
        source=f"Greenhouse:{token}",
    )


class FakeClock:
    def __init__(self, wall_time: float = 1_700_000_000.0) -> None:
        self.elapsed = 0.0
        self.epoch = wall_time
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.elapsed

    def time(self) -> float:
        return self.epoch + self.elapsed

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.elapsed += seconds


def test_prefilters_with_bilingual_title_and_location_aliases(tmp_path: Path):
    jobs = [
        _job("1", "BI Intern", "Beijing, China", "Use Python, Excel and SQL."),
        _job("2", "Mechanical Engineer", "Shanghai, China", "Use SolidWorks."),
    ]
    discovery = GreenhouseDiscovery(
        catalog=(BoardSpec("alpha", "Alpha"),),
        cache_dir=tmp_path,
        fetcher=lambda _token, _timeout: jobs,
        min_request_interval_seconds=0,
    )

    report = discovery.discover(
        DiscoveryQuery(
            target_titles=("数据分析实习生",),
            locations=("北京",),
            resume_keywords=("Python", "Excel"),
        )
    )

    assert [hit.job.id for hit in report.hits] == ["1"]
    assert report.hits[0].matched_titles == ("数据分析实习生",)
    assert report.hits[0].matched_locations == ("北京",)
    assert report.hits[0].matched_keywords == ("Python", "Excel")
    assert report.hits[0].score == 100


def test_one_board_failure_is_isolated_and_live_requests_are_throttled(tmp_path: Path):
    clock = FakeClock()
    calls: list[str] = []

    def fetch(token: str, _timeout: float):
        calls.append(token)
        if token == "broken":
            raise RuntimeError("temporary upstream failure")
        return [_job("1", "Python Engineer", "Remote", "Python", token=token)]

    discovery = GreenhouseDiscovery(
        catalog=(BoardSpec("broken", "Broken"), BoardSpec("healthy", "Healthy")),
        cache_dir=tmp_path,
        fetcher=fetch,
        min_request_interval_seconds=1.0,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        wall_time=clock.time,
    )
    report = discovery.discover(DiscoveryQuery(resume_keywords=("Python",)))

    assert calls == ["broken", "healthy"]
    assert clock.sleeps == [1.0]
    assert [hit.job.id for hit in report.hits] == ["1"]
    assert report.boards_succeeded == ("healthy",)
    assert report.failures[0].token == "broken"
    assert not report.failures[0].used_stale_cache


def test_fresh_disk_cache_avoids_another_network_request(tmp_path: Path):
    clock = FakeClock()
    calls: list[str] = []

    def fetch(token: str, _timeout: float):
        calls.append(token)
        return [_job("1", "Python Engineer", "Shanghai", "Python", token=token)]

    options = dict(
        catalog=(BoardSpec("alpha", "Alpha"),),
        cache_dir=tmp_path,
        fetcher=fetch,
        cache_ttl_seconds=60,
        min_request_interval_seconds=0,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        wall_time=clock.time,
    )
    query = DiscoveryQuery(resume_keywords=("Python",))
    first = GreenhouseDiscovery(**options).discover(query)
    second = GreenhouseDiscovery(**options).discover(query)

    assert calls == ["alpha"]
    assert first.cache_hits == ()
    assert second.cache_hits == ("alpha",)
    assert second.jobs[0].title == "Python Engineer"


def test_stale_cache_is_used_when_refresh_fails(tmp_path: Path):
    first_clock = FakeClock(wall_time=100.0)
    catalog = (BoardSpec("alpha", "Alpha"),)
    query = DiscoveryQuery(resume_keywords=("Python",))
    GreenhouseDiscovery(
        catalog=catalog,
        cache_dir=tmp_path,
        fetcher=lambda token, _timeout: [
            _job("1", "Python Engineer", "Shanghai", "Python", token=token)
        ],
        cache_ttl_seconds=10,
        min_request_interval_seconds=0,
        wall_time=first_clock.time,
    ).discover(query)

    second_clock = FakeClock(wall_time=120.0)

    def fail(_token: str, _timeout: float):
        raise RuntimeError("offline")

    report = GreenhouseDiscovery(
        catalog=catalog,
        cache_dir=tmp_path,
        fetcher=fail,
        cache_ttl_seconds=10,
        min_request_interval_seconds=0,
        wall_time=second_clock.time,
    ).discover(query)

    assert report.jobs[0].id == "1"
    assert report.stale_cache_used == ("alpha",)
    assert report.boards_succeeded == ("alpha",)
    assert report.failures[0].used_stale_cache


def test_only_allowlisted_tokens_can_be_selected(tmp_path: Path):
    discovery = GreenhouseDiscovery(
        catalog=(BoardSpec("alpha", "Alpha"),),
        cache_dir=tmp_path,
        fetcher=lambda _token, _timeout: [],
        min_request_interval_seconds=0,
    )
    query = DiscoveryQuery(minimum_keyword_hits=0)

    with pytest.raises(UnknownBoardError, match="内置目录"):
        discovery.discover(query, board_tokens=("unknown",))
    with pytest.raises(UnknownBoardError, match="内置"):
        discovery.discover(
            query,
            board_tokens=("https://job-boards.greenhouse.io/alpha",),
        )


def test_ascii_keyword_matching_does_not_match_inside_another_word(tmp_path: Path):
    discovery = GreenhouseDiscovery(
        catalog=(BoardSpec("alpha", "Alpha"),),
        cache_dir=tmp_path,
        fetcher=lambda _token, _timeout: [
            _job("1", "Marketing Intern", "Remote", "Run paid media campaigns")
        ],
        min_request_interval_seconds=0,
    )
    report = discovery.discover(DiscoveryQuery(resume_keywords=("AI",)))

    assert report.hits == ()


def test_reviewed_catalog_is_broad_unique_and_china_apac_first():
    tokens = [board.token for board in DEFAULT_BOARD_CATALOG]

    assert len(CHINA_APAC_BOARD_CATALOG) >= 25
    assert len(DEFAULT_BOARD_CATALOG) >= 40
    assert len(tokens) == len(set(tokens))
    assert {
        "sesai",
        "casetify",
        "guidepoint",
        "mongodb",
        "databricks",
        "agoda",
        "appier",
        "okx",
    }.issubset(tokens)
    assert tokens[: len(CHINA_APAC_BOARD_CATALOG)] == [
        board.token for board in CHINA_APAC_BOARD_CATALOG
    ]


def test_default_selection_prioritizes_query_relevance_and_defers_rest(
    tmp_path: Path,
):
    calls: list[str] = []
    catalog = (
        BoardSpec("global", "Global", ("全球",), ("software",)),
        BoardSpec("factory", "Factory", ("中国",), ("manufacturing",)),
        BoardSpec("china-data", "China Data", ("中国",), ("data",)),
        BoardSpec("hk-data", "Hong Kong Data", ("香港",), ("data",)),
    )

    def fetch(token: str, _timeout: float):
        calls.append(token)
        return [
            _job(
                token,
                "Data Engineer",
                "Shanghai, China",
                "Data platform",
                token=token,
            )
        ]

    report = GreenhouseDiscovery(
        catalog=catalog,
        cache_dir=tmp_path,
        fetcher=fetch,
        max_boards_per_run=2,
        min_request_interval_seconds=0,
    ).discover(
        DiscoveryQuery(
            locations=("中国",),
            resume_keywords=("data",),
            minimum_keyword_hits=0,
        )
    )

    assert calls == ["china-data", "factory"]
    assert report.boards_queried == ("china-data", "factory")
    assert report.boards_deferred == ("hk-data", "global")
    assert len(report.jobs) == 2


def test_total_job_cap_stops_additional_boards_and_reports_truncation(
    tmp_path: Path,
):
    calls: list[str] = []
    catalog = tuple(
        BoardSpec(token, token.title()) for token in ("alpha", "beta", "gamma")
    )

    def fetch(token: str, _timeout: float):
        calls.append(token)
        return [
            _job(f"{token}-1", "Engineer", "China", "Python", token=token),
            _job(f"{token}-2", "Engineer", "China", "Python", token=token),
        ]

    report = GreenhouseDiscovery(
        catalog=catalog,
        cache_dir=tmp_path,
        fetcher=fetch,
        max_total_jobs_per_run=3,
        min_request_interval_seconds=0,
    ).discover(DiscoveryQuery(minimum_keyword_hits=0))

    assert calls == ["alpha", "beta"]
    assert report.total_jobs_seen == 3
    assert len(report.jobs) == 3
    assert report.jobs_truncated
    assert report.boards_queried == ("alpha", "beta")
    assert report.boards_deferred == ("gamma",)


def test_explicit_board_selection_still_enforces_fanout_limit(tmp_path: Path):
    discovery = GreenhouseDiscovery(
        catalog=(BoardSpec("alpha", "Alpha"), BoardSpec("beta", "Beta")),
        cache_dir=tmp_path,
        fetcher=lambda _token, _timeout: [],
        max_boards_per_run=1,
        min_request_interval_seconds=0,
    )

    with pytest.raises(DiscoveryError, match="单次最多读取 1"):
        discovery.discover(
            DiscoveryQuery(minimum_keyword_hits=0),
            board_tokens=("alpha", "beta"),
        )
