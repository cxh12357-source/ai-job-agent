"""Shared primitives for read-only public ATS job sources.

The source adapters in this package deliberately expose only published job
data.  They never send application data and never call an ATS write endpoint.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Any, Mapping, Protocol
from urllib.parse import quote, urlsplit

import requests
from bs4 import BeautifulSoup

from ai_job_agent.models import JobPosting


class HTTPSession(Protocol):
    """Small requests-compatible surface used for dependency injection."""

    def get(self, url: str, **kwargs: Any) -> Any: ...


class SourceRequestError(RuntimeError):
    """Raised internally when a public source response is unusable."""


@dataclass(frozen=True, slots=True)
class SourceFailure:
    """One isolated source/page/record failure.

    A failed page stops pagination for that account, while already collected
    jobs remain usable.  A failed record or detail request does not discard
    other records from the same page.
    """

    source: str
    account: str
    stage: str
    message: str
    page: int | None = None
    job_id: str | None = None

    def __str__(self) -> str:
        context = self.stage
        if self.page is not None:
            context += f" page={self.page}"
        if self.job_id:
            context += f" job={self.job_id}"
        return f"{self.source}/{self.account} ({context}): {self.message}"


@dataclass(frozen=True, slots=True)
class SourceDiscoveryResult:
    """Bounded discovery output with diagnostics for transparent UI reporting."""

    source: str
    account: str
    jobs: tuple[JobPosting, ...]
    failures: tuple[SourceFailure, ...] = ()
    pages_fetched: int = 0
    total_seen: int = 0
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.jobs) or not self.failures


class JobSourceAdapter(ABC):
    """Base class for bounded, read-only ATS discovery adapters."""

    source_name = "ATS"
    default_page_size = 100
    maximum_page_size = 100

    def __init__(
        self,
        account: str,
        *,
        company: str | None = None,
        session: HTTPSession | None = None,
        timeout: float = 12.0,
        page_size: int | None = None,
        max_pages: int = 5,
        max_jobs: int = 500,
    ) -> None:
        self.account = validate_account_identifier(account)
        self.company = clean_text(company) or self.account
        self.session: HTTPSession = session or requests.Session()
        self.timeout = _positive_float(timeout, "timeout")
        self.max_pages = _positive_int(max_pages, "max_pages")
        self.max_jobs = _positive_int(max_jobs, "max_jobs")
        requested_size = page_size or self.default_page_size
        self.page_size = min(
            _positive_int(requested_size, "page_size"), self.maximum_page_size
        )

    @abstractmethod
    def discover(self) -> SourceDiscoveryResult:
        """Fetch published jobs without mutating the ATS or submitting forms."""

    def fetch_jobs(self) -> list[JobPosting]:
        """Convenience jobs-only view for callers that do not need diagnostics."""

        return list(self.discover().jobs)

    def _get_json(
        self, url: str, *, params: Mapping[str, Any] | None = None
    ) -> Any:
        try:
            response = self.session.get(
                url,
                params=dict(params or {}),
                timeout=self.timeout,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "AI-Job-Agent/0.1 (read-only job discovery)",
                },
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # requests and injected clients vary by type
            raise SourceRequestError(safe_error_message(exc)) from exc

    def _failure(
        self,
        stage: str,
        error: Exception | str,
        *,
        page: int | None = None,
        job_id: str | None = None,
    ) -> SourceFailure:
        return SourceFailure(
            source=self.source_name,
            account=self.account,
            stage=stage,
            message=safe_error_message(error),
            page=page,
            job_id=job_id,
        )


def validate_account_identifier(value: str) -> str:
    """Validate an ATS path identifier while preserving case-sensitive slugs."""

    cleaned = str(value or "").strip()
    if not cleaned or len(cleaned) > 160:
        raise ValueError("ATS account identifier must contain 1-160 characters")
    if any(char in cleaned for char in ("/", "\\", "?", "#", "\x00")):
        raise ValueError("ATS account identifier cannot contain URL separators")
    if any(ord(char) < 32 for char in cleaned):
        raise ValueError("ATS account identifier cannot contain control characters")
    return cleaned


def quoted_account(value: str) -> str:
    return quote(validate_account_identifier(value), safe="-._~")


def clean_html(value: Any) -> str:
    """Convert ATS HTML fragments to compact readable plain text."""

    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    for element in soup(["script", "style", "noscript", "template", "svg"]):
        element.decompose()
    for element in soup.find_all("br"):
        element.replace_with("\n")
    for element in soup.find_all(
        [
            "address",
            "article",
            "blockquote",
            "div",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "li",
            "p",
            "section",
            "tr",
        ]
    ):
        element.insert_before("\n")
        element.append("\n")
    lines: list[str] = []
    for raw_line in soup.get_text(" ", strip=False).splitlines():
        line = " ".join(raw_line.split())
        line = re.sub(r"\s+([,.;:!?%)\]}>，。；：！？])", r"\1", line)
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines)


def clean_text(value: Any) -> str:
    """Normalize plain or HTML-ish values without preserving markup."""

    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    if "<" not in raw and not re.search(r"&(?:#\d+|#x[\da-f]+|[a-z]+);", raw, re.I):
        return "\n".join(
            " ".join(line.split()) for line in raw.splitlines() if line.strip()
        )
    return clean_html(raw)


def join_unique(values: list[Any] | tuple[Any, ...], separator: str = " / ") -> str:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = clean_text(value)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return separator.join(result)


def safe_https_url(value: Any, *, fallback: str = "") -> str:
    """Accept only absolute HTTPS navigation targets returned by an ATS."""

    candidate = str(value or "").strip()
    try:
        parts = urlsplit(candidate)
    except ValueError:
        parts = None
    if (
        parts
        and parts.scheme.casefold() == "https"
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
    ):
        return candidate
    return fallback


def stable_job_id(
    source: str,
    account: str,
    native_id: Any = None,
    *,
    fingerprint: tuple[Any, ...] = (),
) -> str:
    """Build a stable namespaced id, hashing deterministic fields if needed."""

    native = clean_text(native_id)
    if not native:
        material = "\x1f".join(clean_text(part) for part in fingerprint)
        native = sha256(material.encode("utf-8")).hexdigest()[:24]
    elif len(native) > 160 or any(ord(char) < 32 for char in native):
        native = sha256(native.encode("utf-8")).hexdigest()[:24]
    return f"{source.casefold()}:{account.casefold()}:{native}"


def require_title(value: Any) -> str:
    title = clean_text(value)
    if not title:
        raise ValueError("job title is missing")
    return title


def safe_error_message(error: Exception | str) -> str:
    message = " ".join(str(error).split()) or type(error).__name__
    return message[:300]


def _positive_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _positive_float(value: Any, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be positive") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


__all__ = [
    "HTTPSession",
    "JobSourceAdapter",
    "SourceDiscoveryResult",
    "SourceFailure",
    "SourceRequestError",
    "clean_html",
    "clean_text",
    "join_unique",
    "quoted_account",
    "require_title",
    "safe_error_message",
    "safe_https_url",
    "stable_job_id",
    "validate_account_identifier",
]
