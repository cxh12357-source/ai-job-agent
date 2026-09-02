from __future__ import annotations

import ipaddress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from ai_job_agent.adapters.generic import GenericAdapter
from ai_job_agent.adapters.base import ApplyDiscoveryPlan

from .blockers import detect_page_blockers
from .filler import FillResult, FormFiller
from .models import FormFillPlan, PageSnapshot
from .redaction import sanitized_error_artifact
from .review import AUTO_SUBMIT, ApplicationReview, evaluate_submit_gate


SNAPSHOT_SCRIPT = r"""
() => {
  const attr = 'data-ai-job-agent-control';
  const visible = (element) => {
    if (!element) return false;
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' &&
      (rect.width > 0 || rect.height > 0);
  };
  const textForIds = (rawIds) => String(rawIds || '').split(/\s+/)
    .map((id) => document.getElementById(id))
    .filter(Boolean)
    .map((element) => element.innerText || element.textContent || '')
    .join(' ');
  const controls = Array.from(document.querySelectorAll('input, select, textarea'));
  const result = controls.map((element, index) => {
    element.setAttribute(attr, String(index));
    const labels = Array.from(element.labels || [])
      .map((label) => label.innerText || label.textContent || '')
      .join(' ');
    const labelled = textForIds(element.getAttribute('aria-labelledby'));
    const described = textForIds(element.getAttribute('aria-describedby'));
    const container = element.closest(
      '.form-field, .field, .question, .application-field, .input-group, li, p, div'
    );
    const nearby = container ? (container.innerText || container.textContent || '') : '';
    const type = String(element.type || element.tagName || 'text').toLowerCase();
    const fileLabelVisible = type === 'file' && Array.from(element.labels || []).some(visible);
    return {
      selector: `[${attr}="${index}"]`,
      tag: String(element.tagName || 'input').toLowerCase(),
      type,
      label: `${labels} ${labelled}`.trim().slice(0, 500),
      placeholder: String(element.getAttribute('placeholder') || '').slice(0, 300),
      name: String(element.getAttribute('name') || '').slice(0, 300),
      id: String(element.id || '').slice(0, 300),
      aria_label: String(element.getAttribute('aria-label') || '').slice(0, 500),
      aria_description: described.trim().slice(0, 500),
      nearby_text: String(nearby).trim().slice(0, 800),
      autocomplete: String(element.getAttribute('autocomplete') || '').toLowerCase(),
      required: Boolean(element.required) || element.getAttribute('aria-required') === 'true' ||
        /\*/.test(labels),
      visible: visible(element) || fileLabelVisible,
      disabled: Boolean(element.disabled)
    };
  });
  return {
    page_url: window.location.href,
    page_text: String(document.body ? document.body.innerText : '').slice(0, 50000),
    frame_urls: Array.from(document.querySelectorAll('iframe'))
      .map((frame) => String(frame.src || '')),
    controls: result
  };
}
"""

LINK_SNAPSHOT_SCRIPT = r"""
() => {
  const visible = (element) => {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' &&
      (rect.width > 0 || rect.height > 0);
  };
  return Array.from(document.querySelectorAll('a[href]')).map((element) => ({
    href: String(element.getAttribute('href') || '').slice(0, 4096),
    text: String(element.innerText || element.textContent || '').trim().slice(0, 500),
    aria_label: String(element.getAttribute('aria-label') || '').slice(0, 500),
    title: String(element.getAttribute('title') || '').slice(0, 500),
    visible: visible(element)
  }));
}
"""


def _is_local_url(value: str) -> bool:
    try:
        host = (urlsplit(value).hostname or "").casefold()
    except ValueError:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class BrowserRunResult:
    status: str
    plan: FormFillPlan | None
    review: ApplicationReview | None
    filled_fields: tuple[str, ...] = ()
    submitted_demo: bool = False
    screenshot_metadata: dict[str, object] | None = None

    def public_summary(self) -> dict[str, object]:
        return {
            "status": self.status,
            "filled_fields": list(self.filled_fields),
            "submitted_demo": self.submitted_demo,
            "review": self.review.public_summary() if self.review else None,
            "screenshot_metadata": self.screenshot_metadata,
            "will_submit_real_site": False,
        }


class BrowserAgent:
    """A headed-or-headless Playwright agent with a hard final-submit boundary."""

    def __init__(
        self,
        *,
        headless: bool = False,
        allow_local_demo: bool = False,
        screenshot_dir: str | Path = "output/browser_errors",
        timeout_ms: int = 15_000,
        adapter: GenericAdapter | None = None,
        filler: FormFiller | None = None,
    ) -> None:
        self.headless = bool(headless)
        self.allow_local_demo = bool(allow_local_demo)
        self.screenshot_dir = Path(screenshot_dir)
        self.timeout_ms = int(timeout_ms)
        self.adapter = adapter or GenericAdapter(allow_local_demo=self.allow_local_demo)
        self.filler = filler or FormFiller()
        self._playwright: Any | None = None
        self.browser: Any | None = None
        self.context: Any | None = None
        self.page: Any | None = None

    def __enter__(self) -> "BrowserAgent":
        self.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def start(self) -> None:
        if self.browser is not None:
            return
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        try:
            self.browser = self._playwright.chromium.launch(headless=self.headless)
            self.context = self.browser.new_context()
            self.page = self.context.new_page()
            self.page.set_default_timeout(self.timeout_ms)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        for target_name in ("context", "browser"):
            target = getattr(self, target_name, None)
            if target is not None:
                try:
                    target.close()
                except Exception:
                    pass
                setattr(self, target_name, None)
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        self.page = None

    def open(self, url: str) -> str:
        self.start()
        assert self.page is not None
        normalized = self.adapter.validate_url(url)
        initial_is_local = _is_local_url(normalized)
        self.page.goto(normalized, wait_until="domcontentloaded")
        final_url = self.adapter.validate_url(self.page.url)
        if initial_is_local != _is_local_url(final_url):
            raise ValueError("navigation changed between local demo and public internet")
        return final_url

    def snapshot(self) -> PageSnapshot:
        if self.page is None:
            raise RuntimeError("browser page is not open")
        raw = self.page.evaluate(SNAPSHOT_SCRIPT)
        frame_urls = list(raw.get("frame_urls", ()))
        frame_urls.extend(frame.url for frame in self.page.frames if frame.url)
        raw["frame_urls"] = list(dict.fromkeys(frame_urls))
        return PageSnapshot.from_mapping(raw)

    def discover_apply_links(self, url: str) -> ApplyDiscoveryPlan:
        """Open a job page and return a no-click Apply-link discovery plan."""

        final_url = self.open(url)
        assert self.page is not None
        links = self.page.evaluate(LINK_SNAPSHOT_SCRIPT)
        return self.adapter.plan_apply_discovery(final_url, links)

    def run_from_job_page(
        self,
        url: str,
        applicant_values: dict[str, object],
        *,
        environment: str = "real",
        review_confirmed: bool = False,
        auto_submit: bool = AUTO_SUBMIT,
    ) -> BrowserRunResult:
        """Enter one high-confidence Apply link, then run the normal fill gate.

        This is a navigation convenience for self-built career pages.  It never
        weakens :meth:`run_application`'s final-submit boundary.
        """

        try:
            final_url = self.open(url)
            snapshot = self.snapshot()
            if snapshot.controls:
                application_url = final_url
            else:
                assert self.page is not None
                links = self.page.evaluate(LINK_SNAPSHOT_SCRIPT)
                discovery = self.adapter.plan_apply_discovery(final_url, links)
                candidate = discovery.preferred_candidate
                if candidate is None or candidate.confidence < 0.85:
                    return BrowserRunResult(
                        "needs_user_confirmation",
                        None,
                        None,
                    )
                application_url = candidate.url
            return self.run_application(
                application_url,
                applicant_values,
                environment=environment,
                review_confirmed=review_confirmed,
                auto_submit=auto_submit,
            )
        except Exception as exc:
            return BrowserRunResult(
                "error",
                None,
                None,
                screenshot_metadata=self._capture_error(exc),
            )

    def _capture_error(self, error: BaseException) -> dict[str, object]:
        page_url = self.page.url if self.page is not None else "https://invalid.invalid/"
        screenshot_path: Path | None = None
        if self.page is not None:
            try:
                self.screenshot_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                screenshot_path = self.screenshot_dir / f"browser-error-{stamp}-{uuid4().hex[:8]}.png"
                # Mask every editable control so the PNG itself does not retain
                # names, contact details, answers or local file paths.
                editable_controls = self.page.locator(
                    "input, textarea, select, [contenteditable='true']"
                )
                self.page.screenshot(
                    path=str(screenshot_path),
                    full_page=True,
                    mask=[editable_controls],
                    mask_color="#000000",
                )
            except Exception:
                screenshot_path = None
        return sanitized_error_artifact(
            page_url=page_url,
            screenshot_path=screenshot_path,
            error=error,
        )

    def run_application(
        self,
        url: str,
        applicant_values: dict[str, object],
        *,
        environment: str = "real",
        review_confirmed: bool = False,
        auto_submit: bool = AUTO_SUBMIT,
    ) -> BrowserRunResult:
        """Open, inspect and fill; only a confirmed local demo may be submitted."""

        plan: FormFillPlan | None = None
        review: ApplicationReview | None = None
        try:
            final_url = self.open(url)
            snapshot = self.snapshot()
            if snapshot.page_url != final_url:
                snapshot = replace(snapshot, page_url=final_url)
            plan = self.adapter.build_form_plan(snapshot, applicant_values)
            review = ApplicationReview.from_plan(plan, environment=environment)
            if review_confirmed:
                review = review.confirm(True)
            if plan.blockers:
                return BrowserRunResult("paused_for_user", plan, review)

            fill_result: FillResult = self.filler.fill(self.page, plan)
            if fill_result.failures:
                error = RuntimeError(
                    "form fill failed for "
                    + ",".join(failure.field_name for failure in fill_result.failures)
                )
                return BrowserRunResult(
                    "error",
                    plan,
                    review,
                    filled_fields=fill_result.filled_fields,
                    screenshot_metadata=self._capture_error(error),
                )

            post_blockers = detect_page_blockers(self.snapshot())
            if post_blockers:
                review = replace(review, blockers=post_blockers)
                return BrowserRunResult(
                    "paused_for_user",
                    plan,
                    review,
                    filled_fields=fill_result.filled_fields,
                )
            if review.pending_confirmations:
                return BrowserRunResult(
                    "needs_user_confirmation",
                    plan,
                    review,
                    filled_fields=fill_result.filled_fields,
                )

            decision = evaluate_submit_gate(review, auto_submit=auto_submit)
            if decision.allowed:
                self.filler.submit_demo(self.page, review, auto_submit=auto_submit)
                return BrowserRunResult(
                    "submitted_demo",
                    plan,
                    review,
                    filled_fields=fill_result.filled_fields,
                    submitted_demo=True,
                )
            status = "ready_for_manual_submit" if environment == "real" else "ready_for_review"
            return BrowserRunResult(
                status,
                plan,
                review,
                filled_fields=fill_result.filled_fields,
            )
        except Exception as exc:
            return BrowserRunResult(
                "error",
                plan,
                review,
                screenshot_metadata=self._capture_error(exc),
            )


__all__ = [
    "BrowserAgent",
    "BrowserRunResult",
    "LINK_SNAPSHOT_SCRIPT",
    "SNAPSHOT_SCRIPT",
]
