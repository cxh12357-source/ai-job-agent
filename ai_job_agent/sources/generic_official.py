"""Safe, read-only discovery for user-supplied official career sites.

This module deliberately does *not* automate search engines, authentication,
CAPTCHAs, or form submission.  It follows a small number of same-origin pages
and converts structured ``JobPosting`` data (or semantic job cards) into the
application's :class:`~ai_job_agent.models.JobPosting` model.

The network boundary is dependency-injected.  Production callers may use the
default ``requests`` fetcher; tests can provide a deterministic local fake
without opening sockets.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from html import unescape
import ipaddress
import json
import re
import socket
from typing import Any, Protocol
from urllib import robotparser
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag

from ai_job_agent.models import JobPosting


USER_AGENT = "AIJobAgent/0.1 (read-only official-career discovery)"
DEFAULT_MAX_PAGES = 8
DEFAULT_MAX_JOBS = 100
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_RESPONSE_BYTES = 2_500_000
PROXY_PROBE_HOST = "example.com"
_DESKTOP_PROXY_NET = ipaddress.ip_network("198.18.0.0/15")


class OfficialSourceError(RuntimeError):
    """Base error for a user-supplied official career source."""


class UnsafeCareerUrlError(OfficialSourceError, ValueError):
    """The supplied or discovered URL could reach a non-public resource."""


class SourceBlockedError(OfficialSourceError):
    """The site requires a human step or explicitly disallows this reader."""


class SourceFetchError(OfficialSourceError):
    """A public page could not be fetched or was not a supported document."""


@dataclass(frozen=True, slots=True)
class FetchResponse:
    """Minimal response contract accepted from an injected fetcher."""

    url: str
    status_code: int
    text: str
    headers: Mapping[str, str] = field(default_factory=dict)
    history: tuple[str, ...] = ()


class Fetcher(Protocol):
    def __call__(self, url: str) -> FetchResponse | Any: ...


Resolver = Callable[[str, int], Sequence[str]]


@dataclass(frozen=True, slots=True)
class SourceDiscoveryResult:
    """Jobs and transparent crawl diagnostics for UI/service integration."""

    jobs: tuple[JobPosting, ...]
    source_url: str
    pages_visited: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def _default_resolver(host: str, port: int) -> Sequence[str]:
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:  # pragma: no cover - depends on the host network
        raise UnsafeCareerUrlError(f"无法确认招聘网站域名是否为公开地址：{host}") from exc
    return tuple(dict.fromkeys(str(item[4][0]) for item in records))


def _default_fetcher(url: str) -> FetchResponse:
    """Fetch one response without automatic redirects.

    Redirect handling stays in :class:`GenericOfficialSource`, so every hop is
    validated before another request is made.
    """

    try:
        response = requests.get(
            url,
            allow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            timeout=(5, 20),
        )
    except requests.RequestException as exc:  # pragma: no cover - network dependent
        raise SourceFetchError(f"无法读取招聘页面：{url}") from exc
    return FetchResponse(
        url=str(response.url or url),
        status_code=int(response.status_code),
        text=response.text,
        headers=dict(response.headers),
        history=tuple(str(item.url) for item in response.history),
    )


def _header(headers: Mapping[str, str], name: str) -> str:
    target = name.casefold()
    for key, value in headers.items():
        if str(key).casefold() == target:
            return str(value)
    return ""


def _coerce_response(value: FetchResponse | Any, requested_url: str) -> FetchResponse:
    if isinstance(value, FetchResponse):
        return value
    if isinstance(value, Mapping):
        return FetchResponse(
            url=str(value.get("url") or requested_url),
            status_code=int(value.get("status_code", value.get("status", 200))),
            text=str(value.get("text", value.get("body", ""))),
            headers=dict(value.get("headers") or {}),
            history=tuple(str(item) for item in (value.get("history") or ())),
        )
    try:
        history = tuple(str(item.url) for item in (getattr(value, "history", ()) or ()))
        return FetchResponse(
            url=str(getattr(value, "url", requested_url) or requested_url),
            status_code=int(getattr(value, "status_code")),
            text=str(getattr(value, "text", "")),
            headers=dict(getattr(value, "headers", {}) or {}),
            history=history,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise SourceFetchError("招聘页面读取器返回了无法识别的响应") from exc


def _effective_port(parts: SplitResult) -> int:
    try:
        explicit = parts.port
    except ValueError as exc:
        raise UnsafeCareerUrlError("招聘页面端口格式无效") from exc
    if explicit is not None:
        return explicit
    return 443 if parts.scheme.casefold() == "https" else 80


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    return parts.scheme.casefold(), (parts.hostname or "").casefold().rstrip("."), _effective_port(parts)


def _is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    # ``is_global`` is the positive allow-list: it excludes private, loopback,
    # link-local, multicast, documentation, reserved, and unspecified ranges.
    return bool(address.is_global)


def _is_desktop_proxy_ip(value: str) -> bool:
    """Whether an address is in RFC 2544's benchmark-only 198.18/15 range.

    Some desktop network sandboxes transparently map every public hostname to
    this range and route the original hostname through their controlled proxy.
    A literal benchmark address is never accepted; the exception is only used
    for DNS results after a fixed public probe confirms that virtualization is
    active on the current resolver.
    """

    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return address.version == 4 and address in _DESKTOP_PROXY_NET


def _normalise_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UnsafeCareerUrlError("请输入企业公开招聘页面 URL")
    if any(ord(character) < 32 for character in value):
        raise UnsafeCareerUrlError("招聘页面 URL 含有控制字符")
    parts = urlsplit(value.strip())
    scheme = parts.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise UnsafeCareerUrlError("招聘页面只支持 http 或 https")
    if parts.username is not None or parts.password is not None:
        raise UnsafeCareerUrlError("招聘页面 URL 不能包含用户名或密码")
    raw_host = parts.hostname
    if not raw_host:
        raise UnsafeCareerUrlError("招聘页面 URL 缺少域名")
    try:
        host = raw_host.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise UnsafeCareerUrlError("招聘页面域名格式无效") from exc
    if host == "localhost" or host.endswith(".localhost"):
        raise UnsafeCareerUrlError("招聘页面必须使用公开域名")
    port = _effective_port(parts)
    expected_port = 443 if scheme == "https" else 80
    if port != expected_port:
        raise UnsafeCareerUrlError("招聘页面只允许公开 Web 标准端口 80/443")
    # Rebuild the authority to remove credentials, mixed case, fragments, and
    # redundant default ports before it is logged, deduplicated, or requested.
    netloc = f"[{host}]" if ":" in host else host
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _clean_text(value: Any, *, limit: int = 20_000) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        value = " ".join(_clean_text(item) for item in (value.values() if isinstance(value, dict) else value))
    soup = BeautifulSoup(unescape(str(value)), "html.parser")
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
    return text[:limit]


def _iter_json_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if graph is not None:
            yield from _iter_json_objects(graph)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_json_objects(item)


def _is_job_posting(value: Mapping[str, Any]) -> bool:
    kinds = value.get("@type")
    if isinstance(kinds, str):
        return kinds.casefold() == "jobposting"
    if isinstance(kinds, list):
        return any(str(item).casefold() == "jobposting" for item in kinds)
    return False


def _jsonld_company(value: Mapping[str, Any], fallback: str) -> str:
    organisation = value.get("hiringOrganization")
    if isinstance(organisation, Mapping):
        name = _clean_text(organisation.get("name"), limit=200)
        if name:
            return name
    return fallback


def _jsonld_location(value: Mapping[str, Any]) -> str | None:
    locations = value.get("jobLocation")
    if not isinstance(locations, list):
        locations = [locations] if locations else []
    found: list[str] = []
    for location in locations:
        if not isinstance(location, Mapping):
            text = _clean_text(location, limit=300)
            if text:
                found.append(text)
            continue
        address = location.get("address", location)
        if isinstance(address, Mapping):
            pieces = [
                _clean_text(address.get(key), limit=120)
                for key in ("addressLocality", "addressRegion", "addressCountry")
            ]
            text = ", ".join(dict.fromkeys(item for item in pieces if item))
        else:
            text = _clean_text(address, limit=300)
        if text:
            found.append(text)
    if str(value.get("jobLocationType", "")).casefold() == "telecommute":
        found.append("Remote")
    merged = " / ".join(dict.fromkeys(found))
    return merged or None


def _identifier(value: Mapping[str, Any], url: str) -> str | None:
    identifier = value.get("identifier")
    if isinstance(identifier, Mapping):
        candidate = identifier.get("value") or identifier.get("name")
    else:
        candidate = identifier
    text = _clean_text(candidate, limit=300)
    if text:
        return text
    segment = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return segment or None


def _employment_type(value: Any) -> str | None:
    if isinstance(value, list):
        text = ", ".join(_clean_text(item, limit=80) for item in value if item)
    else:
        text = _clean_text(value, limit=160)
    return text or None


_JOB_PATH_RE = re.compile(
    r"(?:/(?:jobs?|positions?|vacanc(?:y|ies)|openings?|job-detail|jobdetail|recruitment)/|"
    r"[?&](?:job_?id|position_?id|posting_?id|vacancy_?id)=)",
    re.IGNORECASE,
)
_SEMANTIC_CONTAINER_RE = re.compile(
    r"(?:job|position|vacanc|opening|career|recruit|职位|岗位|招聘)", re.IGNORECASE
)
_TITLE_SIGNAL_RE = re.compile(
    r"(?:engineer|developer|analyst|scientist|designer|manager|specialist|consultant|"
    r"intern|graduate|new grad|director|architect|researcher|工程师|开发|算法|分析师|设计师|"
    r"经理|专员|顾问|实习|管培|应届|毕业生|研究员|产品|运营|职位|岗位)",
    re.IGNORECASE,
)
_GENERIC_LINK_TEXT = {
    "job",
    "jobs",
    "career",
    "careers",
    "open positions",
    "view jobs",
    "view job",
    "details",
    "learn more",
    "apply",
    "apply now",
    "招聘",
    "校园招聘",
    "校招",
    "应届生招聘",
    "毕业生招聘",
    "职位",
    "岗位",
    "职位列表",
    "查看详情",
    "立即申请",
    "申请",
}


def _looks_like_job_url(url: str) -> bool:
    return bool(_JOB_PATH_RE.search(url))


def _best_link_title(anchor: Tag) -> str:
    heading = anchor.find(["h1", "h2", "h3", "h4", "h5", "h6"])
    raw = heading.get_text(" ", strip=True) if heading else anchor.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", raw).strip()[:240]


def _card_value(anchor: Tag, token: str) -> str | None:
    card = anchor.find_parent(
        ["article", "section", "li", "tr", "div"], class_=_SEMANTIC_CONTAINER_RE
    )
    if not card:
        return None
    found = card.find(class_=re.compile(token, re.IGNORECASE))
    text = _clean_text(found.get_text(" ", strip=True), limit=300) if found else ""
    return text or None


def _site_name(soup: BeautifulSoup, hostname: str) -> str:
    meta = soup.find("meta", attrs={"property": "og:site_name"})
    if isinstance(meta, Tag):
        name = _clean_text(meta.get("content"), limit=200)
        if name:
            return name
    labels = hostname.split(".")
    candidate = labels[-2] if len(labels) >= 2 else labels[0]
    return candidate.replace("-", " ").strip().title() or hostname


def _blocker_reason(soup: BeautifulSoup, url: str, status_code: int) -> str | None:
    if status_code in {401, 403}:
        return "网站要求登录或拒绝自动读取"
    if status_code == 429:
        return "网站请求频率受限，请稍后由用户重试"
    path = urlsplit(url).path.casefold()
    if re.search(r"/(?:login|signin|sign-in|auth|captcha|challenge)(?:/|$)", path):
        return "网站需要人工登录或验证"
    if soup.select_one(
        "input[type='password'], iframe[src*='captcha' i], iframe[src*='recaptcha' i], "
        "[class*='captcha' i], [id*='captcha' i], [data-sitekey]"
    ):
        return "网站需要人工登录或完成 CAPTCHA"
    title = _clean_text(soup.title.get_text(" ", strip=True) if soup.title else "", limit=300).casefold()
    challenge_markers = (
        "verify you are human",
        "security check",
        "captcha",
        "人机验证",
        "安全验证",
        "滑块验证",
        "请输入验证码",
    )
    if any(marker in title for marker in challenge_markers):
        return "网站需要人工完成验证"
    return None


def _page_has_nofollow(soup: BeautifulSoup) -> bool:
    meta = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.IGNORECASE)})
    return isinstance(meta, Tag) and "nofollow" in str(meta.get("content", "")).casefold()


def _merge_job(old: JobPosting | None, new: JobPosting) -> JobPosting:
    if old is None:
        return new
    old_data = old.model_dump()
    new_data = new.model_dump()
    merged: dict[str, Any] = {}
    for key in old_data:
        incoming = new_data.get(key)
        current = old_data.get(key)
        if key in {"description", "requirements"}:
            incoming_size = len(str(incoming or ""))
            current_size = len(str(current or ""))
            merged[key] = incoming if incoming_size > current_size else current
        else:
            merged[key] = incoming if incoming not in (None, "", [], ()) else current
    return JobPosting.model_validate(merged)


class GenericOfficialSource:
    """Discover jobs from one public, user-specified, same-origin career site."""

    def __init__(
        self,
        *,
        fetcher: Fetcher | None = None,
        resolver: Resolver | None = None,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_jobs: int = DEFAULT_MAX_JOBS,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self.fetcher = fetcher or _default_fetcher
        self.resolver = resolver or _default_resolver
        self.max_pages = max(1, min(int(max_pages), 50))
        self.max_jobs = max(1, min(int(max_jobs), 500))
        self.max_redirects = max(0, min(int(max_redirects), 10))
        self.max_response_bytes = max(10_000, min(int(max_response_bytes), 10_000_000))
        self._robots: dict[tuple[str, str, int], robotparser.RobotFileParser] = {}
        self._desktop_proxy_active: bool | None = None

    def _detect_desktop_proxy(self, probe_addresses: Sequence[str] | None = None) -> bool:
        """Confirm benchmark-address virtualization with a fixed public probe.

        The narrow exception is enabled only when *all* addresses returned for
        ``example.com`` are inside 198.18.0.0/15.  A mixed answer containing a
        loopback, private, link-local, or other reserved address fails closed.
        The result is cached for this source instance.
        """

        if self._desktop_proxy_active is not None:
            return self._desktop_proxy_active
        try:
            addresses = tuple(
                str(item)
                for item in (
                    probe_addresses
                    if probe_addresses is not None
                    else self.resolver(PROXY_PROBE_HOST, 443)
                )
            )
        except Exception:
            addresses = ()
        self._desktop_proxy_active = bool(addresses) and all(
            _is_desktop_proxy_ip(item) for item in addresses
        )
        return self._desktop_proxy_active

    def _validate_public_url(self, value: str) -> str:
        url = _normalise_url(value)
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = _effective_port(parts)
        try:
            ipaddress.ip_address(host.split("%", 1)[0])
            is_ip_literal = True
        except ValueError:
            is_ip_literal = False
        if is_ip_literal:
            addresses: Sequence[str] = (host,)
        else:
            try:
                addresses = self.resolver(host, port)
            except UnsafeCareerUrlError:
                raise
            except Exception as exc:
                raise UnsafeCareerUrlError(f"无法确认招聘网站域名是否为公开地址：{host}") from exc
        string_addresses = tuple(str(item) for item in addresses)
        has_proxy_address = any(_is_desktop_proxy_ip(item) for item in string_addresses)
        has_disallowed_address = any(
            not _is_public_ip(item) and not _is_desktop_proxy_ip(item)
            for item in string_addresses
        )
        probe_addresses = string_addresses if host == PROXY_PROBE_HOST else None
        proxy_mapping_is_safe = (
            not is_ip_literal
            and has_proxy_address
            and self._detect_desktop_proxy(probe_addresses)
        )
        if (
            not string_addresses
            or has_disallowed_address
            or (has_proxy_address and not proxy_mapping_is_safe)
        ):
            raise UnsafeCareerUrlError("招聘页面解析到了本机、私网或保留地址")
        return url

    def _request(
        self,
        value: str,
        allowed_origin: tuple[str, str, int],
        *,
        robots: robotparser.RobotFileParser | None = None,
    ) -> FetchResponse:
        current = self._validate_public_url(value)
        for redirect_count in range(self.max_redirects + 1):
            if _origin(current) != allowed_origin:
                raise UnsafeCareerUrlError("只允许读取用户指定招聘网站的同源页面")
            # A redirect can change the path while staying on the same host.
            # Apply robots rules to every target *before* fetching that hop.
            if robots is not None and not robots.can_fetch(USER_AGENT, current):
                raise SourceBlockedError("robots.txt 不允许读取重定向后的招聘页面")
            try:
                response = _coerce_response(self.fetcher(current), current)
            except OfficialSourceError:
                raise
            except Exception as exc:
                raise SourceFetchError(f"无法读取招聘页面：{current}") from exc

            # An injected client may have followed redirects itself.  Validate
            # every reported hop and the final URL before examining its body.
            for hop in (*response.history, response.url):
                safe_hop = self._validate_public_url(hop)
                if _origin(safe_hop) != allowed_origin:
                    raise UnsafeCareerUrlError("招聘网站重定向到了不同来源，已停止读取")

            if response.status_code in {301, 302, 303, 307, 308}:
                location = _header(response.headers, "location")
                if not location:
                    raise SourceFetchError("招聘网站返回重定向但未提供目标地址")
                if redirect_count >= self.max_redirects:
                    raise SourceFetchError("招聘网站重定向次数过多")
                # Validation happens at the top of the next iteration *before*
                # the redirected resource can be fetched.
                current = urljoin(current, location)
                continue

            body_bytes = len(response.text.encode("utf-8", errors="replace"))
            if body_bytes > self.max_response_bytes:
                raise SourceFetchError("招聘页面内容过大，已停止读取")
            return FetchResponse(
                url=self._validate_public_url(response.url or current),
                status_code=response.status_code,
                text=response.text,
                headers=response.headers,
                history=response.history,
            )
        raise SourceFetchError("招聘网站重定向次数过多")  # pragma: no cover

    def _robots_for(self, origin_url: str) -> robotparser.RobotFileParser:
        origin_key = _origin(origin_url)
        cached = self._robots.get(origin_key)
        if cached is not None:
            return cached
        scheme, host, port = origin_key
        authority = f"[{host}]" if ":" in host else host
        if port not in {80, 443}:  # Defensive; public-port validation already enforces this.
            authority = f"{authority}:{port}"
        robots_url = f"{scheme}://{authority}/robots.txt"
        response = self._request(robots_url, origin_key)
        parser = robotparser.RobotFileParser(robots_url)
        if response.status_code in {401, 403}:
            raise SourceBlockedError("网站拒绝读取 robots.txt，已停止抓取")
        if response.status_code == 429:
            raise SourceBlockedError("网站请求频率受限，请稍后重试")
        if 200 <= response.status_code < 300:
            parser.parse(response.text.splitlines())
        elif response.status_code == 404:
            parser.parse([])
        else:
            raise SourceFetchError(f"无法确认网站抓取规则（HTTP {response.status_code}）")
        self._robots[origin_key] = parser
        return parser

    def _fetch_page(self, url: str, origin_key: tuple[str, str, int]) -> FetchResponse:
        robots = self._robots_for(url)
        if not robots.can_fetch(USER_AGENT, url):
            raise SourceBlockedError("robots.txt 不允许读取该招聘页面")
        response = self._request(url, origin_key, robots=robots)
        if response.status_code in {401, 403, 429}:
            raise SourceBlockedError(
                "网站需要登录、人工验证或稍后重试，程序不会尝试绕过限制"
            )
        if response.status_code == 404:
            return response
        if not 200 <= response.status_code < 300:
            raise SourceFetchError(f"招聘页面读取失败（HTTP {response.status_code}）")
        content_type = _header(response.headers, "content-type").casefold()
        if content_type and not any(
            allowed in content_type
            for allowed in ("text/html", "application/xhtml+xml", "text/plain", "application/json")
        ):
            raise SourceFetchError("招聘页面不是可解析的 HTML/JSON 文档")
        return response

    def _safe_same_origin_link(
        self, href: str, base_url: str, origin_key: tuple[str, str, int]
    ) -> str | None:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            return None
        try:
            candidate = _normalise_url(urljoin(base_url, href))
        except UnsafeCareerUrlError:
            return None
        return candidate if _origin(candidate) == origin_key else None

    def _jsonld_jobs(
        self,
        soup: BeautifulSoup,
        page_url: str,
        origin_key: tuple[str, str, int],
        company_fallback: str,
    ) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
            raw = script.string or script.get_text()
            if not raw.strip():
                continue
            try:
                payload = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            for item in _iter_json_objects(payload):
                if not _is_job_posting(item):
                    continue
                title = _clean_text(item.get("title") or item.get("name"), limit=300)
                if not title:
                    continue
                raw_url = str(item.get("url") or page_url)
                job_url = self._safe_same_origin_link(raw_url, page_url, origin_key)
                if not job_url:
                    continue
                requirements_parts = [
                    _clean_text(item.get(key))
                    for key in ("qualifications", "experienceRequirements", "skills", "responsibilities")
                ]
                requirements = "\n".join(dict.fromkeys(part for part in requirements_parts if part)) or None
                jobs.append(
                    JobPosting(
                        job_id=_identifier(item, job_url),
                        title=title,
                        company=_jsonld_company(item, company_fallback),
                        location=_jsonld_location(item),
                        job_type=_employment_type(item.get("employmentType")),
                        department=_clean_text(item.get("industry"), limit=300) or None,
                        description=_clean_text(item.get("description")),
                        requirements=requirements,
                        job_url=job_url,
                        source=f"Official website:{origin_key[1]}",
                        publish_date=_clean_text(
                            item.get("datePosted") or item.get("validThrough"), limit=120
                        )
                        or None,
                    )
                )
        return jobs

    def _semantic_jobs(
        self,
        soup: BeautifulSoup,
        page_url: str,
        origin_key: tuple[str, str, int],
        company: str,
    ) -> tuple[list[JobPosting], list[str]]:
        jobs: list[JobPosting] = []
        detail_urls: list[str] = []
        for anchor in soup.find_all("a", href=True):
            if not isinstance(anchor, Tag):
                continue
            job_url = self._safe_same_origin_link(str(anchor.get("href")), page_url, origin_key)
            if not job_url:
                continue
            title = _best_link_title(anchor)
            folded = title.casefold().strip(" -—|›»")
            if not title or len(title) > 200 or folded in _GENERIC_LINK_TEXT:
                continue
            semantic_parent = anchor.find_parent(
                ["article", "section", "li", "tr", "div"],
                class_=_SEMANTIC_CONTAINER_RE,
            )
            semantic_anchor = _SEMANTIC_CONTAINER_RE.search(" ".join(anchor.get("class", [])))
            if not (
                _looks_like_job_url(job_url)
                or semantic_parent
                or semantic_anchor
            ):
                continue
            if not (_looks_like_job_url(job_url) or _TITLE_SIGNAL_RE.search(title)):
                continue
            jobs.append(
                JobPosting(
                    job_id=urlsplit(job_url).path.rstrip("/").rsplit("/", 1)[-1] or None,
                    title=title,
                    company=company,
                    location=_card_value(anchor, r"location|city|地点|城市"),
                    department=_card_value(anchor, r"department|team|category|部门|团队|类别"),
                    job_url=job_url,
                    source=f"Official website:{origin_key[1]}",
                )
            )
            detail_urls.append(job_url)
        return jobs, detail_urls

    def _detail_fallback(
        self,
        soup: BeautifulSoup,
        page_url: str,
        origin_key: tuple[str, str, int],
        company: str,
    ) -> JobPosting | None:
        if not _looks_like_job_url(page_url):
            return None
        heading = soup.find("h1")
        title = _clean_text(heading.get_text(" ", strip=True), limit=300) if heading else ""
        if not title or title.casefold() in _GENERIC_LINK_TEXT:
            return None
        content = soup.select_one(
            "[class*='job-description' i], [id*='job-description' i], "
            "[class*='description' i], article, main"
        )
        location_node = soup.select_one("[class*='location' i], [data-location]")
        department_node = soup.select_one("[class*='department' i], [class*='team' i]")
        return JobPosting(
            job_id=urlsplit(page_url).path.rstrip("/").rsplit("/", 1)[-1] or None,
            title=title,
            company=company,
            location=_clean_text(
                location_node.get("data-location") or location_node.get_text(" ", strip=True),
                limit=300,
            )
            if location_node
            else None,
            department=_clean_text(department_node.get_text(" ", strip=True), limit=300)
            if department_node
            else None,
            description=_clean_text(content.get_text(" ", strip=True)) if content else "",
            job_url=page_url,
            source=f"Official website:{origin_key[1]}",
        )

    def _pagination_links(
        self,
        soup: BeautifulSoup,
        page_url: str,
        origin_key: tuple[str, str, int],
    ) -> list[str]:
        found: list[str] = []
        for anchor in soup.find_all("a", href=True):
            if not isinstance(anchor, Tag):
                continue
            rel = {str(item).casefold() for item in (anchor.get("rel") or [])}
            text = _clean_text(anchor.get_text(" ", strip=True), limit=80).casefold()
            parent = anchor.find_parent(["nav", "div", "ul"], class_=re.compile("pagin", re.I))
            href = str(anchor.get("href"))
            is_next = "next" in rel or text in {"next", "next page", "下一页", "更多职位", "more jobs"}
            is_pagination = bool(parent and re.search(r"[?&](?:page|p)=\d+", href, re.I))
            if not (is_next or is_pagination):
                continue
            safe = self._safe_same_origin_link(href, page_url, origin_key)
            if safe:
                found.append(safe)
        return list(dict.fromkeys(found))

    def discover(self, url: str) -> SourceDiscoveryResult:
        """Read a bounded set of same-origin public pages and return jobs.

        Login, verification, CAPTCHA, robots exclusions, private-network hops,
        and cross-origin redirects stop the affected read; none are bypassed.
        """

        start_url = self._validate_public_url(url)
        origin_key = _origin(start_url)
        pending: deque[str] = deque([start_url])
        queued = {start_url}
        visited: list[str] = []
        warnings: list[str] = []
        jobs_by_url: dict[str, JobPosting] = {}

        while pending and len(visited) < self.max_pages and len(jobs_by_url) < self.max_jobs:
            page_url = pending.popleft()
            try:
                response = self._fetch_page(page_url, origin_key)
            except SourceBlockedError:
                if not visited:
                    raise
                warnings.append(f"已跳过需要人工验证/登录或被 robots 禁止的页面：{page_url}")
                continue
            visited.append(page_url)
            if response.status_code == 404:
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            blocker = _blocker_reason(soup, response.url, response.status_code)
            if blocker:
                if len(visited) == 1:
                    raise SourceBlockedError(f"{blocker}；程序不会尝试绕过")
                warnings.append(f"{blocker}，已跳过：{response.url}")
                continue

            company = _site_name(soup, origin_key[1])
            structured = self._jsonld_jobs(soup, response.url, origin_key, company)
            for job in structured:
                jobs_by_url[job.job_url] = _merge_job(jobs_by_url.get(job.job_url), job)
                if len(jobs_by_url) >= self.max_jobs:
                    break
            if len(jobs_by_url) >= self.max_jobs:
                break

            semantic, detail_urls = self._semantic_jobs(soup, response.url, origin_key, company)
            for job in semantic:
                jobs_by_url[job.job_url] = _merge_job(jobs_by_url.get(job.job_url), job)
                if len(jobs_by_url) >= self.max_jobs:
                    break
            if not structured:
                fallback = self._detail_fallback(soup, response.url, origin_key, company)
                if fallback:
                    jobs_by_url[fallback.job_url] = _merge_job(
                        jobs_by_url.get(fallback.job_url), fallback
                    )

            if _page_has_nofollow(soup):
                warnings.append(f"页面声明 nofollow，未继续访问其中链接：{response.url}")
                continue
            candidates = [*detail_urls, *self._pagination_links(soup, response.url, origin_key)]
            for candidate in candidates:
                if candidate not in queued and len(queued) < self.max_pages * 5:
                    queued.add(candidate)
                    pending.append(candidate)

        return SourceDiscoveryResult(
            jobs=tuple(list(jobs_by_url.values())[: self.max_jobs]),
            source_url=start_url,
            pages_visited=tuple(visited),
            warnings=tuple(warnings),
        )


def discover_official_jobs(
    url: str,
    *,
    fetcher: Fetcher | None = None,
    resolver: Resolver | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_jobs: int = DEFAULT_MAX_JOBS,
) -> list[JobPosting]:
    """Convenience jobs-only API for the existing search service."""

    result = GenericOfficialSource(
        fetcher=fetcher,
        resolver=resolver,
        max_pages=max_pages,
        max_jobs=max_jobs,
    ).discover(url)
    return list(result.jobs)


__all__ = [
    "DEFAULT_MAX_JOBS",
    "DEFAULT_MAX_PAGES",
    "FetchResponse",
    "Fetcher",
    "GenericOfficialSource",
    "OfficialSourceError",
    "PROXY_PROBE_HOST",
    "SourceBlockedError",
    "SourceDiscoveryResult",
    "SourceFetchError",
    "UnsafeCareerUrlError",
    "discover_official_jobs",
]
