"""Browser-agnostic form inspection and review primitives."""

from .blockers import detect_page_blockers
from .agent import BrowserAgent, BrowserRunResult
from .field_detector import FieldDetector
from .filler import FillResult, FormFiller
from .models import (
    ConfirmationItem,
    FieldMatch,
    FillAction,
    FormControl,
    FormFillPlan,
    PageBlocker,
    PageSnapshot,
)
from .planner import FormPlanner
from .review import (
    AUTO_SUBMIT,
    ApplicationReview,
    SubmitDecision,
    evaluate_submit_gate,
)

__all__ = [
    "AUTO_SUBMIT",
    "ApplicationReview",
    "BrowserAgent",
    "BrowserRunResult",
    "ConfirmationItem",
    "FieldDetector",
    "FieldMatch",
    "FillResult",
    "FillAction",
    "FormControl",
    "FormFillPlan",
    "FormFiller",
    "FormPlanner",
    "PageBlocker",
    "PageSnapshot",
    "SubmitDecision",
    "detect_page_blockers",
    "evaluate_submit_gate",
]
