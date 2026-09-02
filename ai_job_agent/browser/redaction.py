from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


REDACTED = "[REDACTED]"
SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "email",
        "first_name",
        "full_name",
        "last_name",
        "password",
        "phone",
        "resume",
        "resume_path",
        "secret",
        "session",
        "token",
        "value",
    }
)
_EMAIL_RE = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
_WINDOWS_USER_RE = re.compile(r"(?i)\b([A-Z]:\\Users\\)[^\\\s]+")
_POSIX_USER_RE = re.compile(r"(?i)(/home/)[^/\s]+")


def redact_text(value: object) -> str:
    text = str(value or "")
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    text = _WINDOWS_USER_RE.sub(r"\1[REDACTED]", text)
    return _POSIX_USER_RE.sub(r"\1[REDACTED]", text)


def sanitize_url(value: object) -> str:
    candidate = str(value or "").strip()
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return "[INVALID_URL]"
    if not parsed.scheme or not parsed.hostname:
        return "[INVALID_URL]"
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        return "[INVALID_URL]"
    netloc = host if port is None else f"{host}:{port}"
    query = "redacted=1" if parsed.query else ""
    return urlunsplit((parsed.scheme, netloc, redact_text(parsed.path), query, ""))


def sanitize_log_metadata(value: Any, *, key: str = "") -> Any:
    normalized_key = str(key).casefold()
    if normalized_key in SENSITIVE_KEYS or any(
        marker in normalized_key
        for marker in ("password", "secret", "token", "cookie", "authorization")
    ):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(child_key): sanitize_log_metadata(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_log_metadata(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return f"[BINARY_REDACTED:{len(value)}]"
    if "url" in normalized_key:
        return sanitize_url(value)
    if "path" in normalized_key or "screenshot" in normalized_key:
        return redact_text(Path(str(value)).name)
    if isinstance(value, str):
        return redact_text(value)
    return value


def sanitized_error_artifact(
    *, page_url: str, screenshot_path: str | Path | None, error: BaseException
) -> dict[str, Any]:
    """Return metadata safe for logs; screenshot pixels are never embedded."""

    return {
        "page_url": sanitize_url(page_url),
        "screenshot_recorded": screenshot_path is not None,
        "screenshot_name": redact_text(Path(screenshot_path).name) if screenshot_path else None,
        "error_type": type(error).__name__,
        "error_message": redact_text(error),
    }


__all__ = [
    "REDACTED",
    "redact_text",
    "sanitize_log_metadata",
    "sanitize_url",
    "sanitized_error_artifact",
]
