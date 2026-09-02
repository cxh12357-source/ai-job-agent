from __future__ import annotations

import ipaddress
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import SplitResult, urlsplit, urlunsplit

from ai_job_agent.browser.models import FormFillPlan, PageSnapshot
from ai_job_agent.browser.redaction import sanitize_url


MAX_URL_LENGTH = 4096
_ENCODED_CONTROL = re.compile(r"%(?:0[ad]|00)", re.IGNORECASE)
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


class AdapterError(RuntimeError):
    pass


class UnsafeCareerUrl(AdapterError):
    pass


def _is_loopback_host(hostname: str) -> bool:
    normalized = hostname.rstrip(".").casefold()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def normalize_career_url(value: str, *, allow_local_demo: bool = False) -> str:
    candidate = str(value or "").strip()
    if not candidate or len(candidate) > MAX_URL_LENGTH:
        raise UnsafeCareerUrl("URL is empty or too long")
    if any(ord(character) < 32 or ord(character) == 127 for character in candidate):
        raise UnsafeCareerUrl("URL contains control characters")
    if "\\" in candidate or _ENCODED_CONTROL.search(candidate):
        raise UnsafeCareerUrl("URL has an unsafe encoding")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeCareerUrl("URL is malformed") from exc
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeCareerUrl("embedded credentials are forbidden")
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    if not hostname:
        raise UnsafeCareerUrl("URL has no host")

    local_target = _is_loopback_host(hostname)
    if local_target:
        if not allow_local_demo or parsed.scheme not in {"http", "https"}:
            raise UnsafeCareerUrl("local URLs are allowed only in demo mode")
    else:
        if parsed.scheme.casefold() != "https":
            raise UnsafeCareerUrl("real career pages must use HTTPS")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise UnsafeCareerUrl("real career pages must use a public domain")
        if hostname.endswith((".local", ".internal")) or "." not in hostname:
            raise UnsafeCareerUrl("private or incomplete host is forbidden")
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise UnsafeCareerUrl("host is invalid") from exc
        if len(ascii_hostname) > 253 or any(
            not _HOST_LABEL.fullmatch(label) for label in ascii_hostname.split(".")
        ):
            raise UnsafeCareerUrl("host is invalid")
        hostname = ascii_hostname
    if not local_target and port not in (None, 443):
        raise UnsafeCareerUrl("non-standard ports are forbidden on real sites")
    if port is not None and not 1 <= port <= 65535:
        raise UnsafeCareerUrl("port is invalid")
    netloc = hostname if port is None else f"{hostname}:{port}"
    return urlunsplit(
        SplitResult(parsed.scheme.casefold(), netloc, parsed.path or "/", parsed.query, "")
    )


@dataclass(frozen=True, slots=True)
class LinkSnapshot:
    href: str = field(repr=False)
    text: str = ""
    aria_label: str = ""
    title: str = ""
    visible: bool = True

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "LinkSnapshot":
        return cls(
            href=str(value.get("href") or ""),
            text=str(value.get("text") or value.get("inner_text") or ""),
            aria_label=str(value.get("aria_label") or value.get("aria-label") or ""),
            title=str(value.get("title") or ""),
            visible=value.get("visible") is not False,
        )


@dataclass(frozen=True, slots=True)
class ApplyCandidate:
    url: str = field(repr=False)
    confidence: float
    evidence: tuple[str, ...]
    needs_user_confirmation: bool

    def public_summary(self) -> dict[str, object]:
        return {
            "url": sanitize_url(self.url),
            "confidence": round(self.confidence, 3),
            "evidence": list(self.evidence),
            "needs_user_confirmation": self.needs_user_confirmation,
        }


@dataclass(frozen=True, slots=True)
class ApplyDiscoveryPlan:
    page_url: str = field(repr=False)
    candidates: tuple[ApplyCandidate, ...]
    rejected_count: int = 0

    @property
    def preferred_candidate(self) -> ApplyCandidate | None:
        return self.candidates[0] if self.candidates else None

    def public_summary(self) -> dict[str, object]:
        return {
            "page_url": sanitize_url(self.page_url),
            "candidates": [candidate.public_summary() for candidate in self.candidates],
            "rejected_count": self.rejected_count,
            "will_click": False,
        }


class BaseCareerAdapter(ABC):
    name = "base"

    @abstractmethod
    def validate_url(self, value: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def plan_apply_discovery(
        self,
        page_url: str,
        links: Sequence[LinkSnapshot | Mapping[str, object]],
    ) -> ApplyDiscoveryPlan:
        raise NotImplementedError

    @abstractmethod
    def build_form_plan(
        self,
        snapshot: PageSnapshot | Mapping[str, object],
        applicant_values: Mapping[str, object],
    ) -> FormFillPlan:
        raise NotImplementedError


__all__ = [
    "AdapterError",
    "ApplyCandidate",
    "ApplyDiscoveryPlan",
    "BaseCareerAdapter",
    "LinkSnapshot",
    "UnsafeCareerUrl",
    "normalize_career_url",
]
