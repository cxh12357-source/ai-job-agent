"""Safe, provider-neutral building blocks for assisted job applications.

The package intentionally separates discovery, form planning, review and final
submission.  Importing it never opens a browser or sends applicant data.
"""

from .browser.review import (
    AUTO_SUBMIT,
    ApplicationReview as BrowserApplicationReview,
    evaluate_submit_gate,
)
from .models import (
    ApplicationReview,
    Award,
    CandidateProfile,
    Certification,
    Education,
    Experience,
    FieldSchema,
    GeneratedAnswer,
    JobPosting,
    Language,
    MatchAssessment,
    Project,
)

__all__ = [
    "AUTO_SUBMIT",
    "ApplicationReview",
    "Award",
    "BrowserApplicationReview",
    "CandidateProfile",
    "Certification",
    "Education",
    "Experience",
    "FieldSchema",
    "GeneratedAnswer",
    "JobPosting",
    "Language",
    "MatchAssessment",
    "Project",
    "evaluate_submit_gate",
]
