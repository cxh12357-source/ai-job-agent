from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .models import PageSnapshot
from .planner import FormPlanner
from .review import AUTO_SUBMIT, ApplicationReview, evaluate_submit_gate


DEMO_PAGE_URL = "http://127.0.0.1:8766/demo/apply"


def build_demo_snapshot() -> PageSnapshot:
    """Return a deterministic mock form; it performs no network activity."""

    return PageSnapshot.from_mapping(
        {
            "page_url": DEMO_PAGE_URL,
            "page_text": "Demo application form",
            "frame_urls": [],
            "controls": [
                {
                    "selector": "#first-name",
                    "name": "candidate_first_name",
                    "label": "First name",
                    "required": True,
                },
                {
                    "selector": "#last-name",
                    "aria_label": "Last name",
                    "required": True,
                },
                {
                    "selector": "#email",
                    "type": "email",
                    "placeholder": "Email address",
                    "required": True,
                },
                {
                    "selector": "#resume",
                    "type": "file",
                    "nearby_text": "Upload resume / CV",
                    "required": True,
                },
            ],
        }
    )


@dataclass(frozen=True, slots=True)
class DemoSubmissionReceipt:
    submitted: bool
    destination: str
    field_count: int


class DemoSubmissionBlocked(RuntimeError):
    pass


class DemoFormMock:
    """An in-memory target used to test the final-submit gate safely."""

    def __init__(self, planner: FormPlanner | None = None) -> None:
        self.planner = planner or FormPlanner()

    def build_review(self, applicant_values: Mapping[str, object]) -> ApplicationReview:
        plan = self.planner.build_plan(build_demo_snapshot(), applicant_values)
        return ApplicationReview.from_plan(plan, environment="demo")

    def submit(
        self,
        review: ApplicationReview,
        *,
        auto_submit: bool = AUTO_SUBMIT,
    ) -> DemoSubmissionReceipt:
        decision = evaluate_submit_gate(review, auto_submit=auto_submit)
        if not decision.allowed:
            raise DemoSubmissionBlocked(decision.reason)
        return DemoSubmissionReceipt(
            submitted=True,
            destination="local_demo_only",
            field_count=len(review.field_names),
        )


__all__ = [
    "DEMO_PAGE_URL",
    "DemoFormMock",
    "DemoSubmissionBlocked",
    "DemoSubmissionReceipt",
    "build_demo_snapshot",
]
