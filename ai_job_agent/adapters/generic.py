from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import urljoin, urlsplit

from ai_job_agent.browser.models import FormFillPlan, PageSnapshot, coerce_snapshot
from ai_job_agent.browser.planner import FormPlanner

from .base import (
    ApplyCandidate,
    ApplyDiscoveryPlan,
    BaseCareerAdapter,
    LinkSnapshot,
    UnsafeCareerUrl,
    _is_loopback_host,
    normalize_career_url,
)


_EXACT_APPLY_TEXT = frozenset(
    {
        "apply",
        "apply now",
        "apply for this job",
        "submit application",
        "申请",
        "立即申请",
        "申请职位",
        "投递",
        "立即投递",
        "提交申请",
    }
)
_APPLY_MARKERS = ("apply", "application", "申请", "投递")
_NEGATIVE_MARKERS = ("login", "log in", "sign in", "登录", "privacy", "隐私")


class GenericAdapter(BaseCareerAdapter):
    """Provider-neutral adapter that proposes actions but never clicks them."""

    name = "generic"

    def __init__(
        self, *, allow_local_demo: bool = False, planner: FormPlanner | None = None
    ) -> None:
        self.allow_local_demo = allow_local_demo
        self.planner = planner or FormPlanner()

    def validate_url(self, value: str) -> str:
        return normalize_career_url(value, allow_local_demo=self.allow_local_demo)

    @staticmethod
    def _score(link: LinkSnapshot) -> tuple[float, tuple[str, ...]]:
        text = " ".join((link.text, link.aria_label, link.title)).strip().casefold()
        href = link.href.casefold()
        if any(marker in text for marker in _NEGATIVE_MARKERS):
            return 0.0, ()
        evidence: list[str] = []
        score = 0.0
        if text in _EXACT_APPLY_TEXT:
            score = 0.98
            evidence.append("exact_apply_text")
        elif any(marker in text for marker in _APPLY_MARKERS):
            score = 0.86
            evidence.append("apply_text")
        if any(marker in href for marker in ("/apply", "application", "jobapply")):
            score = max(score, 0.74)
            evidence.append("apply_href")
        if len(evidence) > 1:
            score = min(1.0, score + 0.02)
        return score, tuple(evidence)

    def plan_apply_discovery(
        self,
        page_url: str,
        links: Sequence[LinkSnapshot | Mapping[str, object]],
    ) -> ApplyDiscoveryPlan:
        normalized_page = self.validate_url(page_url)
        page_host = (urlsplit(normalized_page).hostname or "").casefold()
        page_is_local = _is_loopback_host(page_host)
        candidates: list[ApplyCandidate] = []
        rejected = 0
        seen: set[str] = set()

        for raw_link in links:
            link = raw_link if isinstance(raw_link, LinkSnapshot) else LinkSnapshot.from_mapping(raw_link)
            if not link.visible or not link.href.strip():
                continue
            score, evidence = self._score(link)
            if score < 0.70:
                continue
            try:
                candidate_url = self.validate_url(urljoin(normalized_page, link.href.strip()))
            except UnsafeCareerUrl:
                rejected += 1
                continue
            candidate_host = (urlsplit(candidate_url).hostname or "").casefold()
            candidate_is_local = _is_loopback_host(candidate_host)
            # A demo can never escape to the public internet, and a public page
            # can never pivot into a loopback target.
            if page_is_local != candidate_is_local:
                rejected += 1
                continue
            if candidate_url in seen:
                continue
            seen.add(candidate_url)
            cross_origin = urlsplit(normalized_page).netloc != urlsplit(candidate_url).netloc
            candidates.append(
                ApplyCandidate(
                    url=candidate_url,
                    confidence=max(0.0, score - (0.08 if cross_origin else 0.0)),
                    evidence=evidence + (("cross_origin",) if cross_origin else ()),
                    needs_user_confirmation=cross_origin,
                )
            )
        candidates.sort(key=lambda item: item.confidence, reverse=True)
        return ApplyDiscoveryPlan(normalized_page, tuple(candidates[:20]), rejected)

    def build_form_plan(
        self,
        snapshot: PageSnapshot | Mapping[str, object],
        applicant_values: Mapping[str, object],
    ) -> FormFillPlan:
        page = coerce_snapshot(snapshot)
        normalized_url = self.validate_url(page.page_url)
        normalized_page = PageSnapshot(
            page_url=normalized_url,
            controls=page.controls,
            page_text=page.page_text,
            frame_urls=page.frame_urls,
        )
        return self.planner.build_plan(normalized_page, applicant_values)


__all__ = ["GenericAdapter"]
