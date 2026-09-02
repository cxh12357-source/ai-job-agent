from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

from .models import ConfirmationItem, FormFillPlan, PageBlocker
from .redaction import sanitize_url


# Safe default.  Production code must not silently replace this with True.
AUTO_SUBMIT = False
DEMO_ENVIRONMENTS = frozenset({"demo", "local_test"})


@dataclass(frozen=True, slots=True)
class ApplicationReview:
    page_url: str = field(repr=False)
    field_names: tuple[str, ...]
    pending_confirmations: tuple[ConfirmationItem, ...]
    blockers: tuple[PageBlocker, ...]
    environment: str = "real"
    review_confirmed: bool = False

    @property
    def answer_generation_requests(self) -> tuple[ConfirmationItem, ...]:
        return tuple(
            item
            for item in self.pending_confirmations
            if item.reason == "answer_generator_required"
        )

    @classmethod
    def from_plan(
        cls,
        plan: FormFillPlan,
        *,
        environment: str = "real",
        review_confirmed: bool = False,
    ) -> "ApplicationReview":
        if environment not in {"real", *DEMO_ENVIRONMENTS}:
            raise ValueError("environment must be real, demo or local_test")
        if type(review_confirmed) is not bool:
            raise TypeError("review_confirmed must be a bool")
        return cls(
            page_url=plan.page_url,
            field_names=tuple(action.field_name for action in plan.actions),
            pending_confirmations=plan.needs_user_confirmation,
            blockers=plan.blockers,
            environment=environment,
            review_confirmed=review_confirmed,
        )

    def confirm(self, confirmed: bool = True) -> "ApplicationReview":
        if type(confirmed) is not bool:
            raise TypeError("confirmed must be a bool")
        return replace(self, review_confirmed=confirmed)

    def resolve_confirmations(self, *field_names: str) -> "ApplicationReview":
        resolved = {str(name) for name in field_names}
        return replace(
            self,
            pending_confirmations=tuple(
                item for item in self.pending_confirmations if item.field_name not in resolved
            ),
        )

    def public_summary(self) -> dict[str, object]:
        return {
            "page_url": sanitize_url(self.page_url),
            "field_names": list(self.field_names),
            "pending_confirmations": [
                item.public_summary() for item in self.pending_confirmations
            ],
            "blockers": [blocker.code for blocker in self.blockers],
            "environment": self.environment,
            "review_confirmed": self.review_confirmed,
            "will_submit": False,
        }


@dataclass(frozen=True, slots=True)
class SubmitDecision:
    allowed: bool
    reason: str


def _is_local_target(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def evaluate_submit_gate(
    review: ApplicationReview, *, auto_submit: bool = AUTO_SUBMIT
) -> SubmitDecision:
    """Allow a final submit only inside an explicitly confirmed local demo."""

    if type(auto_submit) is not bool:
        return SubmitDecision(False, "auto_submit_must_be_bool")
    if review.environment not in DEMO_ENVIRONMENTS:
        return SubmitDecision(False, "real_site_never_auto_submits")
    if not _is_local_target(review.page_url):
        return SubmitDecision(False, "demo_target_must_be_local")
    if not auto_submit:
        return SubmitDecision(False, "auto_submit_disabled")
    if review.review_confirmed is not True:
        return SubmitDecision(False, "review_not_confirmed")
    if review.blockers:
        return SubmitDecision(False, "page_blocked")
    if review.pending_confirmations:
        return SubmitDecision(False, "user_confirmation_pending")
    return SubmitDecision(True, "confirmed_local_demo")


__all__ = [
    "AUTO_SUBMIT",
    "ApplicationReview",
    "SubmitDecision",
    "evaluate_submit_gate",
]
