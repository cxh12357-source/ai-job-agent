from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .field_detector import FILLABLE_FIELDS
from .models import FormFillPlan
from .review import AUTO_SUBMIT, ApplicationReview, evaluate_submit_gate


ALLOWED_RESUME_SUFFIXES = frozenset({".pdf", ".doc", ".docx"})
MAX_RESUME_BYTES = 10 * 1024 * 1024
DEMO_SUBMIT_SELECTOR = '[data-demo-submit="true"]'


@dataclass(frozen=True, slots=True)
class FillFailure:
    field_name: str
    error_type: str


@dataclass(frozen=True, slots=True)
class FillResult:
    filled_fields: tuple[str, ...]
    failures: tuple[FillFailure, ...] = ()


class DemoSubmitBlocked(RuntimeError):
    pass


def _local_origin(value: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").casefold()
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    if host == "localhost" or host.endswith(".localhost"):
        return parsed.scheme, parsed.netloc.casefold()
    try:
        if ipaddress.ip_address(host).is_loopback:
            return parsed.scheme, parsed.netloc.casefold()
    except ValueError:
        pass
    return None


def _validate_resume_path(value: str) -> Path:
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("resume file does not exist") from exc
    if not path.is_file() or path.suffix.casefold() not in ALLOWED_RESUME_SUFFIXES:
        raise ValueError("resume must be a PDF, DOC or DOCX file")
    size = path.stat().st_size
    if not 0 < size <= MAX_RESUME_BYTES:
        raise ValueError("resume must be non-empty and no larger than 10 MB")
    return path


class FormFiller:
    """Execute only the allow-listed actions in a previously reviewed plan."""

    def fill(self, page: Any, plan: FormFillPlan) -> FillResult:
        if plan.blockers:
            return FillResult(())
        filled: list[str] = []
        failures: list[FillFailure] = []
        for action in plan.actions:
            try:
                if action.field_name not in FILLABLE_FIELDS:
                    raise ValueError("field is not allow-listed")
                locator = page.locator(action.selector).first
                if locator.count() == 0:
                    raise LookupError("control is no longer present")
                if action.input_type == "file":
                    resume_path = _validate_resume_path(action.value)
                    locator.set_input_files(str(resume_path))
                elif action.input_type in {"checkbox", "radio", "submit", "button", "image"}:
                    raise ValueError("control type requires manual action")
                elif action.input_type in {"select-one", "select-multiple"}:
                    try:
                        locator.select_option(value=action.value)
                    except Exception:
                        locator.select_option(label=action.value)
                else:
                    locator.fill(action.value)
                filled.append(action.field_name)
            except Exception as exc:
                # Never include selector, value, filename or exception text in a
                # public result.  The error type is enough for diagnostics.
                failures.append(FillFailure(action.field_name, type(exc).__name__))
        return FillResult(tuple(filled), tuple(failures))

    def submit_demo(
        self,
        page: Any,
        review: ApplicationReview,
        *,
        auto_submit: bool = AUTO_SUBMIT,
    ) -> None:
        """Click one hard-coded local-demo button after the shared safety gate."""

        decision = evaluate_submit_gate(review, auto_submit=auto_submit)
        if not decision.allowed:
            raise DemoSubmitBlocked(decision.reason)
        actual_origin = _local_origin(str(getattr(page, "url", "")))
        review_origin = _local_origin(review.page_url)
        if actual_origin is None or actual_origin != review_origin:
            raise DemoSubmitBlocked("browser_page_is_not_the_reviewed_local_demo")
        locator = page.locator(DEMO_SUBMIT_SELECTOR)
        if locator.count() != 1:
            raise DemoSubmitBlocked("demo_submit_control_missing_or_ambiguous")
        locator.click()
        page.wait_for_function(
            "() => document.body.dataset.demoSubmitted === 'true'", timeout=5_000
        )


__all__ = [
    "DEMO_SUBMIT_SELECTOR",
    "DemoSubmitBlocked",
    "FillFailure",
    "FillResult",
    "FormFiller",
]
