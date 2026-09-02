"""Polite, read-only discovery across a reviewed Greenhouse board catalog.

This module deliberately stops at discovering and pre-filtering public jobs.  It
does not open application forms, upload resumes, submit applications, bypass
CAPTCHAs, or work around a board's access controls.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import unicodedata
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Callable, Iterable, Sequence
from urllib.parse import urlsplit, urlunsplit

from .models import Job
from .sources.greenhouse import GreenhouseSource, SourceError, extract_board_token


DEFAULT_CACHE_DIR = (
    Path(__file__).resolve().parents[1] / "data" / "cache" / "greenhouse"
)
MAX_CACHE_BYTES = 20 * 1024 * 1024
MAX_JOBS_PER_BOARD = 500
# A deliberately bounded fan-out.  The catalog can be larger than this so the
# query-aware selector can prefer China/APAC and role-relevant boards without
# contacting every company on each refresh.
MAX_BOARDS_PER_RUN = 40
MAX_TOTAL_JOBS_PER_RUN = 8_000
_CACHE_SCHEMA_VERSION = 1


class DiscoveryError(ValueError):
    """Discovery configuration is unsafe or invalid."""


class UnknownBoardError(DiscoveryError):
    """A caller requested a token outside the configured allow-list."""


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value)).casefold()
    return re.sub(r"\s+", " ", value).strip()


def _clean_terms(values: Iterable[str] | str) -> tuple[str, ...]:
    if isinstance(values, str):
        values = values.split(",")
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value).strip()
        normalized = _normalize(item)
        if item and normalized not in seen:
            cleaned.append(item)
            seen.add(normalized)
    return tuple(cleaned)


@dataclass(frozen=True, slots=True)
class BoardSpec:
    """An allow-listed public Greenhouse board."""

    token: str
    company: str
    regions: tuple[str, ...] = ()
    focus: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        token = str(self.token).strip()
        if token != extract_board_token(token) or "://" in token:
            raise DiscoveryError("board 目录中只能使用 Greenhouse board token")
        company = str(self.company).strip()
        if not company:
            raise DiscoveryError("board 目录中的公司名不能为空")
        object.__setattr__(self, "token", token)
        object.__setattr__(self, "company", company)
        object.__setattr__(self, "regions", _clean_terms(self.regions))
        object.__setattr__(self, "focus", _clean_terms(self.focus))


# Every entry below is an explicitly reviewed public Greenhouse board.  Keeping
# this as data (instead of discovering board tokens by crawling/search engines)
# makes the scope auditable and prevents accidental traffic to arbitrary hosts.
# China/APAC boards come first as a deterministic tie-breaker for the bounded
# default selection.
CHINA_APAC_BOARD_CATALOG: tuple[BoardSpec, ...] = (
    BoardSpec("sesai", "SES AI", ("中国",), ("battery", "thermal", "AI")),
    BoardSpec("casetify", "CASETiFY", ("中国", "香港"), ("data", "operations", "product")),
    BoardSpec("guidepoint", "Guidepoint", ("中国", "新加坡"), ("research", "data", "operations")),
    BoardSpec("mongodb", "MongoDB", ("中国", "香港", "新加坡"), ("software", "data", "AI")),
    BoardSpec("adyen", "Adyen", ("中国", "新加坡", "亚洲"), ("payments", "data", "engineering")),
    BoardSpec("databricks", "Databricks", ("中国", "新加坡", "亚洲"), ("data", "AI", "software")),
    BoardSpec("agoda", "Agoda", ("中国", "新加坡", "亚洲"), ("data", "technology", "operations")),
    BoardSpec("appier", "Appier", ("中国", "新加坡", "亚洲"), ("AI", "data", "operations")),
    BoardSpec("impact", "impact.com", ("中国", "亚洲"), ("software", "data", "operations")),
    BoardSpec("forter", "Forter", ("中国", "新加坡"), ("risk", "data", "software")),
    BoardSpec("xendit", "Xendit", ("香港", "新加坡", "亚洲"), ("AI", "data", "automation")),
    BoardSpec("straitsx", "StraitsX", ("新加坡", "亚洲"), ("AI", "data", "payments")),
    BoardSpec("prophet", "Prophet", ("中国", "香港"), ("consulting", "strategy", "design")),
    BoardSpec("starchild", "StarChild", ("中国",), ("consumer", "operations", "product")),
    BoardSpec("maravailifesciences", "Maravai LifeSciences", ("中国",), ("biotechnology", "manufacturing", "quality")),
    BoardSpec("peakdesign", "Peak Design", ("中国",), ("manufacturing", "product")),
    BoardSpec(
        "quberesearchandtechnologies",
        "Qube Research & Technologies",
        ("中国", "香港"),
        ("data", "technology"),
    ),
    BoardSpec("rzr", "RZR", ("中国",), ("BI", "data", "operations")),
    BoardSpec("worldquant", "WorldQuant", ("中国",), ("data", "research")),
    BoardSpec("fictiv", "Fictiv", ("中国",), ("manufacturing", "supply chain")),
    BoardSpec(
        "sharkninjaoperatingllc",
        "SharkNinja",
        ("中国",),
        ("product", "manufacturing"),
    ),
    BoardSpec("mw-tech-grad", "Marshall Wace Technology", ("香港",), ("graduate", "technology")),
    BoardSpec("okx", "OKX", ("亚洲", "香港", "新加坡"), ("software", "data", "product")),
    BoardSpec("thetradedesk", "The Trade Desk", ("中国", "新加坡", "亚洲"), ("advertising", "data", "software")),
    BoardSpec("ideo", "IDEO", ("中国", "亚洲"), ("design", "product", "strategy")),
    BoardSpec("eclipsetrading", "Eclipse Trading", ("香港", "亚洲"), ("trading", "technology", "graduate")),
    BoardSpec("testendouble", "ACT Group", ("中国", "新加坡", "亚洲"), ("energy", "trading", "sustainability")),
    BoardSpec("moloco", "Moloco", ("新加坡", "亚洲"), ("AI", "data", "software")),
    BoardSpec("thoughtworks", "Thoughtworks", ("亚洲",), ("software", "data", "consulting")),
)

GLOBAL_TECH_BOARD_CATALOG: tuple[BoardSpec, ...] = (
    BoardSpec("anthropic", "Anthropic", ("新加坡", "全球"), ("AI", "research", "software")),
    BoardSpec("stripe", "Stripe", ("新加坡", "亚洲", "全球"), ("payments", "data", "software")),
    BoardSpec("cloudflare", "Cloudflare", ("亚洲", "全球"), ("network", "security", "software")),
    BoardSpec("datadog", "Datadog", ("新加坡", "亚洲", "全球"), ("software", "data", "cloud")),
    BoardSpec("twilio", "Twilio", ("新加坡", "亚洲", "全球"), ("software", "communications", "data")),
    BoardSpec("braze", "Braze", ("新加坡", "亚洲", "全球"), ("software", "data", "marketing")),
    BoardSpec("amplitude", "Amplitude", ("新加坡", "亚洲", "全球"), ("data", "product", "software")),
    BoardSpec("elastic", "Elastic", ("亚洲", "全球"), ("search", "data", "software")),
    BoardSpec("gitlab", "GitLab", ("亚洲", "远程", "全球"), ("software", "security", "product")),
    BoardSpec("grafanalabs", "Grafana Labs", ("亚洲", "远程", "全球"), ("data", "cloud", "software")),
    BoardSpec("canonical", "Canonical", ("亚洲", "远程", "全球"), ("software", "cloud", "AI")),
    BoardSpec("scaleai", "Scale AI", ("亚洲", "全球"), ("AI", "data", "software")),
    BoardSpec("taboola", "Taboola", ("亚洲", "全球"), ("data", "advertising", "software")),
    BoardSpec("cockroachlabs", "Cockroach Labs", ("亚洲", "远程", "全球"), ("database", "cloud", "software")),
    BoardSpec("figma", "Figma", ("亚洲", "全球"), ("design", "product", "software")),
    BoardSpec("klaviyo", "Klaviyo", ("亚洲", "全球"), ("data", "marketing", "software")),
    BoardSpec("remotecom", "Remote", ("香港", "新加坡", "远程", "全球"), ("operations", "software", "product")),
)

DEFAULT_BOARD_CATALOG: tuple[BoardSpec, ...] = (
    *CHINA_APAC_BOARD_CATALOG,
    *GLOBAL_TECH_BOARD_CATALOG,
)

CHINA_APAC_BOARD_TOKENS: tuple[str, ...] = tuple(
    board.token for board in CHINA_APAC_BOARD_CATALOG
)


@dataclass(frozen=True, slots=True)
class DiscoveryQuery:
    """Coarse filters applied before the app's full explainable matcher.

    Target-title and location filters are OR-within-a-field and AND-across
    fields.  Resume keywords add relevance and, by default, require one hit.
    """

    resume_keywords: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    target_titles: tuple[str, ...] = ()
    minimum_keyword_hits: int = 1
    limit: int = 100

    def __post_init__(self) -> None:
        object.__setattr__(self, "resume_keywords", _clean_terms(self.resume_keywords))
        object.__setattr__(self, "locations", _clean_terms(self.locations))
        object.__setattr__(self, "target_titles", _clean_terms(self.target_titles))
        if isinstance(self.minimum_keyword_hits, bool) or not isinstance(
            self.minimum_keyword_hits, int
        ):
            raise DiscoveryError("minimum_keyword_hits 必须是非负整数")
        if self.minimum_keyword_hits < 0:
            raise DiscoveryError("minimum_keyword_hits 必须是非负整数")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int):
            raise DiscoveryError("limit 必须是 1 到 500 的整数")
        if not 1 <= self.limit <= 500:
            raise DiscoveryError("limit 必须是 1 到 500 的整数")


@dataclass(frozen=True, slots=True)
class DiscoveryHit:
    job: Job
    score: int
    matched_titles: tuple[str, ...]
    matched_locations: tuple[str, ...]
    matched_keywords: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BoardFailure:
    token: str
    company: str
    message: str
    used_stale_cache: bool = False


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    hits: tuple[DiscoveryHit, ...]
    failures: tuple[BoardFailure, ...]
    boards_queried: tuple[str, ...]
    boards_succeeded: tuple[str, ...]
    cache_hits: tuple[str, ...]
    stale_cache_used: tuple[str, ...]
    total_jobs_seen: int
    boards_deferred: tuple[str, ...] = ()
    jobs_truncated: bool = False

    @property
    def jobs(self) -> tuple[Job, ...]:
        """Convenience view for callers that do not need pre-filter evidence."""

        return tuple(hit.job for hit in self.hits)


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    jobs: tuple[Job, ...]
    fresh: bool


Fetcher = Callable[[str, float], Sequence[Job]]


_TITLE_ALIASES: dict[str, tuple[str, ...]] = {
    "数据分析": ("data analyst", "data analytics", "data science", "business intelligence", "bi"),
    "数据分析实习生": ("data analyst intern", "data science intern", "bi intern"),
    "热管理工程师": ("thermal engineer", "thermal management engineer"),
    "机械工程师": ("mechanical engineer",),
    "制造工程师": ("manufacturing engineer",),
    "工艺工程师": ("process engineer",),
    "测试工程师": ("test engineer",),
    "自动化工程师": ("automation engineer",),
    "ai应用": ("ai engineer", "ai automation", "llm engineer"),
}

_LOCATION_ALIASES: dict[str, tuple[str, ...]] = {
    "中国": ("china",),
    "上海": ("shanghai",),
    "北京": ("beijing",),
    "深圳": ("shenzhen",),
    "广州": ("guangzhou",),
    "苏州": ("suzhou",),
    "东莞": ("dongguan",),
    "香港": ("hong kong",),
    "新加坡": ("singapore",),
    "亚洲": ("asia", "apac", "asia pacific"),
    "全球": ("global", "worldwide"),
    "远程": ("remote",),
}


def _contains(text: str, term: str) -> bool:
    """Match CJK by substring and ASCII tokens without `ai`/`paid` collisions."""

    normalized_text = _normalize(text)
    normalized_term = _normalize(term)
    if not normalized_term:
        return False
    if re.fullmatch(r"[a-z0-9+#. /_-]+", normalized_term):
        pattern = re.escape(normalized_term).replace(r"\ ", r"\s+")
        return bool(
            re.search(
                rf"(?<![a-z0-9]){pattern}(?![a-z0-9])",
                normalized_text,
            )
        )
    return normalized_term in normalized_text


def _aliases_for(term: str, aliases: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    normalized = _normalize(term)
    direct = aliases.get(normalized, ())
    if direct:
        return (term, *direct)
    # English input should also benefit from the reverse bilingual mapping.
    reverse: list[str] = []
    for source, targets in aliases.items():
        if any(normalized == _normalize(target) for target in targets):
            reverse.append(source)
            reverse.extend(targets)
    return (term, *reverse)


def _matched_terms(
    terms: tuple[str, ...], text: str, aliases: dict[str, tuple[str, ...]] | None = None
) -> tuple[str, ...]:
    matched: list[str] = []
    for term in terms:
        candidates = _aliases_for(term, aliases) if aliases else (term,)
        if any(_contains(text, candidate) for candidate in candidates):
            matched.append(term)
    return tuple(matched)


def _score_job(job: Job, query: DiscoveryQuery) -> DiscoveryHit | None:
    title_hits = _matched_terms(query.target_titles, job.title, _TITLE_ALIASES)
    if query.target_titles and not title_hits:
        return None

    location_hits = _matched_terms(query.locations, job.location, _LOCATION_ALIASES)
    if query.locations and not location_hits:
        return None

    keyword_hits = _matched_terms(query.resume_keywords, job.searchable_text)
    required_hits = query.minimum_keyword_hits
    if query.resume_keywords and len(keyword_hits) < required_hits:
        return None

    title_points = 45 if query.target_titles else 0
    location_points = 20 if query.locations else 0
    keyword_points = (
        round(35 * len(keyword_hits) / len(query.resume_keywords))
        if query.resume_keywords
        else 0
    )
    return DiscoveryHit(
        job=job,
        score=min(100, title_points + location_points + keyword_points),
        matched_titles=title_hits,
        matched_locations=location_hits,
        matched_keywords=keyword_hits,
    )


def _board_relevance(board: BoardSpec, query: DiscoveryQuery) -> int:
    """Rank the reviewed catalog without excluding lower-scoring boards.

    This only controls which bounded set is contacted first.  Actual jobs still
    have to pass :func:`prefilter_jobs`, so directory metadata can never create
    a false-positive result.
    """

    region_text = " ".join(board.regions)
    focus_text = " ".join((*board.focus, board.company))
    query_signal_text = " ".join((*query.target_titles, *query.resume_keywords))

    location_hits = _matched_terms(query.locations, region_text, _LOCATION_ALIASES)
    title_hits = _matched_terms(query.target_titles, focus_text, _TITLE_ALIASES)
    focus_hits = sum(
        1 for focus in board.focus if _contains(query_signal_text, focus)
    )
    keyword_hits = _matched_terms(query.resume_keywords, focus_text)

    # Location is the strongest directory-level signal, followed by the role
    # direction.  Stable catalog order resolves ties in favor of China/APAC.
    return (
        100 * len(location_hits)
        + 25 * len(title_hits)
        + 10 * focus_hits
        + 5 * len(keyword_hits)
    )


def prefilter_jobs(
    jobs: Iterable[Job], query: DiscoveryQuery
) -> tuple[DiscoveryHit, ...]:
    """Return deterministic, de-duplicated coarse matches."""

    unique: dict[str, DiscoveryHit] = {}
    for job in jobs:
        hit = _score_job(job, query)
        if hit is None:
            continue
        parsed = urlsplit(job.url)
        canonical_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        key = canonical_url.casefold() or f"{job.source}:{job.id}".casefold()
        previous = unique.get(key)
        if previous is None or hit.score > previous.score:
            unique[key] = hit
    ranked = sorted(
        unique.values(),
        key=lambda item: (
            -item.score,
            item.job.title.casefold(),
            item.job.company.casefold(),
            item.job.id,
        ),
    )
    return tuple(ranked[: query.limit])


def _default_fetcher(token: str, timeout_seconds: float) -> Sequence[Job]:
    return GreenhouseSource(timeout_seconds=timeout_seconds).fetch(token)


class GreenhouseDiscovery:
    """Aggregate allow-listed public boards with bounded, polite requests."""

    def __init__(
        self,
        *,
        catalog: Sequence[BoardSpec] = DEFAULT_BOARD_CATALOG,
        cache_dir: str | Path | None = DEFAULT_CACHE_DIR,
        cache_ttl_seconds: float = 6 * 60 * 60,
        timeout_seconds: float = 12.0,
        min_request_interval_seconds: float = 0.75,
        max_boards_per_run: int = MAX_BOARDS_PER_RUN,
        max_total_jobs_per_run: int = MAX_TOTAL_JOBS_PER_RUN,
        allow_stale_on_error: bool = True,
        fetcher: Fetcher | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self.catalog = tuple(catalog)
        tokens = [board.token for board in self.catalog]
        if len(set(tokens)) != len(tokens):
            raise DiscoveryError("board 目录中存在重复 token")
        for name, value in (
            ("cache_ttl_seconds", cache_ttl_seconds),
            ("timeout_seconds", timeout_seconds),
            ("min_request_interval_seconds", min_request_interval_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DiscoveryError(f"{name} 必须是有限非负数")
            if not math.isfinite(float(value)) or value < 0:
                raise DiscoveryError(f"{name} 必须是有限非负数")
        if timeout_seconds == 0:
            raise DiscoveryError("timeout_seconds 必须大于 0")
        if isinstance(max_boards_per_run, bool) or not isinstance(
            max_boards_per_run, int
        ):
            raise DiscoveryError("max_boards_per_run 必须是 1 到 40 的整数")
        if not 1 <= max_boards_per_run <= MAX_BOARDS_PER_RUN:
            raise DiscoveryError("max_boards_per_run 必须是 1 到 40 的整数")
        if isinstance(max_total_jobs_per_run, bool) or not isinstance(
            max_total_jobs_per_run, int
        ):
            raise DiscoveryError("max_total_jobs_per_run 必须是 1 到 8000 的整数")
        if not 1 <= max_total_jobs_per_run <= MAX_TOTAL_JOBS_PER_RUN:
            raise DiscoveryError("max_total_jobs_per_run 必须是 1 到 8000 的整数")

        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.cache_ttl_seconds = float(cache_ttl_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self.min_request_interval_seconds = float(min_request_interval_seconds)
        self.max_boards_per_run = max_boards_per_run
        self.max_total_jobs_per_run = max_total_jobs_per_run
        self.allow_stale_on_error = bool(allow_stale_on_error)
        self.fetcher = fetcher or _default_fetcher
        self._sleep = sleep
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._last_request_started: float | None = None

    def discover(
        self,
        query: DiscoveryQuery,
        *,
        board_tokens: Sequence[str] | None = None,
    ) -> DiscoveryReport:
        if not isinstance(query, DiscoveryQuery):
            raise DiscoveryError("query 必须是 DiscoveryQuery")
        boards, initially_deferred = self._select_boards(query, board_tokens)
        all_jobs: list[Job] = []
        failures: list[BoardFailure] = []
        succeeded: list[str] = []
        cache_hits: list[str] = []
        stale_used: list[str] = []
        queried: list[str] = []
        deferred = list(initially_deferred)
        jobs_truncated = False

        for index, board in enumerate(boards):
            if len(all_jobs) >= self.max_total_jobs_per_run:
                deferred.extend(item.token for item in boards[index:])
                jobs_truncated = True
                break
            queried.append(board.token)
            cache_entry = self._read_cache(board.token)
            if cache_entry is not None and cache_entry.fresh:
                jobs_truncated = self._extend_jobs_bounded(
                    all_jobs, cache_entry.jobs
                ) or jobs_truncated
                succeeded.append(board.token)
                cache_hits.append(board.token)
                continue

            try:
                self._wait_for_request_slot()
                fetched = self.fetcher(board.token, self.timeout_seconds)
                jobs = self._validate_jobs(fetched)
                jobs_truncated = self._extend_jobs_bounded(
                    all_jobs, jobs
                ) or jobs_truncated
                succeeded.append(board.token)
                self._write_cache(board.token, jobs)
            except Exception as exc:  # one board must never abort all other boards
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                use_stale = bool(
                    self.allow_stale_on_error
                    and cache_entry is not None
                    and cache_entry.jobs
                )
                if use_stale:
                    jobs_truncated = self._extend_jobs_bounded(
                        all_jobs, cache_entry.jobs
                    ) or jobs_truncated
                    succeeded.append(board.token)
                    stale_used.append(board.token)
                failures.append(
                    BoardFailure(
                        token=board.token,
                        company=board.company,
                        message=_safe_error_message(exc),
                        used_stale_cache=use_stale,
                    )
                )

        return DiscoveryReport(
            hits=prefilter_jobs(all_jobs, query),
            failures=tuple(failures),
            boards_queried=tuple(queried),
            boards_succeeded=tuple(succeeded),
            cache_hits=tuple(cache_hits),
            stale_cache_used=tuple(stale_used),
            total_jobs_seen=len(all_jobs),
            boards_deferred=tuple(dict.fromkeys(deferred)),
            jobs_truncated=jobs_truncated,
        )

    def _select_boards(
        self,
        query: DiscoveryQuery,
        board_tokens: Sequence[str] | None,
    ) -> tuple[tuple[BoardSpec, ...], tuple[str, ...]]:
        by_token = {board.token: board for board in self.catalog}
        if board_tokens is None:
            ranked = tuple(
                board
                for _index, board in sorted(
                    enumerate(self.catalog),
                    key=lambda item: (-_board_relevance(item[1], query), item[0]),
                )
            )
            selected = ranked[: self.max_boards_per_run]
            deferred = tuple(
                board.token for board in ranked[self.max_boards_per_run :]
            )
        else:
            selected_list: list[BoardSpec] = []
            seen: set[str] = set()
            for raw_token in board_tokens:
                token = str(raw_token).strip()
                try:
                    normalized = extract_board_token(token)
                except SourceError as exc:
                    raise UnknownBoardError("只能选择内置 Greenhouse board token") from exc
                if token != normalized or token not in by_token:
                    raise UnknownBoardError(f"board token 未在内置目录中：{token}")
                if token not in seen:
                    selected_list.append(by_token[token])
                    seen.add(token)
            selected = tuple(selected_list)
            deferred = ()
            if len(selected) > self.max_boards_per_run:
                raise DiscoveryError(
                    f"单次最多读取 {self.max_boards_per_run} 个 board，请缩小范围"
                )
        return tuple(selected), deferred

    def _extend_jobs_bounded(
        self, destination: list[Job], jobs: Sequence[Job]
    ) -> bool:
        remaining = self.max_total_jobs_per_run - len(destination)
        destination.extend(jobs[:remaining])
        return len(jobs) > remaining

    def _wait_for_request_slot(self) -> None:
        now = self._monotonic()
        if self._last_request_started is not None:
            remaining = self.min_request_interval_seconds - (
                now - self._last_request_started
            )
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_started = self._monotonic()

    def _cache_path(self, token: str) -> Path | None:
        if self.cache_dir is None:
            return None
        # Tokens were already allow-listed and validated; no user path is used.
        return self.cache_dir / f"{token}.json"

    def _read_cache(self, token: str) -> _CacheEntry | None:
        path = self._cache_path(token)
        if path is None:
            return None
        try:
            if not path.is_file() or path.stat().st_size > MAX_CACHE_BYTES:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("schema_version") != _CACHE_SCHEMA_VERSION
                or payload.get("token") != token
                or not isinstance(payload.get("jobs"), list)
            ):
                return None
            cached_at = float(payload.get("cached_at"))
            if not math.isfinite(cached_at):
                return None
            jobs = self._validate_jobs(
                tuple(_job_from_cache(item) for item in payload["jobs"])
            )
            age = max(0.0, self._wall_time() - cached_at)
            return _CacheEntry(
                jobs=jobs,
                fresh=age <= self.cache_ttl_seconds,
            )
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    def _write_cache(self, token: str, jobs: tuple[Job, ...]) -> None:
        path = self._cache_path(token)
        if path is None:
            return
        payload = {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "token": token,
            "cached_at": self._wall_time(),
            "jobs": [asdict(job) for job in jobs],
        }
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if len(data.encode("utf-8")) > MAX_CACHE_BYTES:
                return
            temporary.write_text(data, encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _validate_jobs(values: Sequence[Job]) -> tuple[Job, ...]:
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise DiscoveryError("Greenhouse 职位读取器返回了无效数据")
        jobs = tuple(values[:MAX_JOBS_PER_BOARD])
        if any(not isinstance(job, Job) for job in jobs):
            raise DiscoveryError("Greenhouse 职位读取器返回了无效职位")
        return jobs


_JOB_FIELD_NAMES = frozenset(field.name for field in fields(Job))


def _job_from_cache(value: object) -> Job:
    if not isinstance(value, dict) or set(value) != _JOB_FIELD_NAMES:
        raise ValueError("invalid cached job")
    return Job(**value)


def _safe_error_message(exc: Exception) -> str:
    message = re.sub(r"\s+", " ", str(exc)).strip()
    if not message:
        message = exc.__class__.__name__
    return message[:300]


__all__ = [
    "BoardFailure",
    "BoardSpec",
    "CHINA_APAC_BOARD_CATALOG",
    "CHINA_APAC_BOARD_TOKENS",
    "DEFAULT_BOARD_CATALOG",
    "DiscoveryError",
    "DiscoveryHit",
    "DiscoveryQuery",
    "DiscoveryReport",
    "GreenhouseDiscovery",
    "GLOBAL_TECH_BOARD_CATALOG",
    "MAX_BOARDS_PER_RUN",
    "MAX_TOTAL_JOBS_PER_RUN",
    "UnknownBoardError",
    "prefilter_jobs",
]
