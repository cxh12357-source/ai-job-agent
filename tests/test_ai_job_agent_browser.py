from __future__ import annotations

from pathlib import Path

import pytest

from ai_job_agent.adapters import GenericAdapter, UnsafeCareerUrl
from ai_job_agent.browser.agent import BrowserAgent
from ai_job_agent.browser import (
    AUTO_SUBMIT,
    ApplicationReview,
    FieldDetector,
    FormPlanner,
    PageSnapshot,
    detect_page_blockers,
    evaluate_submit_gate,
)
from ai_job_agent.browser.demo import DemoFormMock, DemoSubmissionBlocked
from ai_job_agent.browser.filler import DemoSubmitBlocked, FormFiller
from ai_job_agent.browser.redaction import sanitize_log_metadata, sanitized_error_artifact


def test_field_detector_uses_multiple_hints_and_maps_multiple_fields():
    detector = FieldDetector(confidence_threshold=0.75)
    controls = [
        {"selector": "#given", "name": "candidate_first_name", "label": "First name"},
        {"selector": "#mail", "type": "email", "placeholder": "Email address"},
        {"selector": "#phone", "aria_label": "Mobile phone"},
        {"selector": "#cv", "type": "file", "nearby_text": "Upload resume / CV"},
    ]

    matches = detector.detect_many(controls)

    assert [match.canonical_field for match in matches] == [
        "first_name",
        "email",
        "phone",
        "resume",
    ]
    assert all(match.needs_user_confirmation is False for match in matches)
    assert set(matches[0].evidence_sources) == {"name", "label"}


def test_sensitive_unknown_and_low_confidence_fields_require_confirmation():
    detector = FieldDetector(confidence_threshold=0.75)
    matches = detector.detect_many(
        [
            {"selector": "#auth", "label": "Are you authorized to work?", "required": True},
            {"selector": "#mystery", "label": "Tell us something", "required": True},
            {"selector": "#near", "nearby_text": "Current location"},
        ]
    )

    assert matches[0].canonical_field == "work_authorization"
    assert matches[0].sensitive is True
    assert matches[0].reason == "sensitive_field"
    assert matches[1].canonical_field is None
    assert matches[1].reason == "unknown_field"
    assert matches[2].canonical_field == "location"
    assert matches[2].reason == "low_confidence"
    assert all(match.needs_user_confirmation for match in matches)


def test_structured_candidate_fields_are_mapped_and_open_answers_are_held_for_review():
    matches = FieldDetector().detect_many(
        [
            {"selector": "#school", "label": "University / School"},
            {"selector": "#degree", "name": "highest_degree"},
            {"selector": "#major", "placeholder": "Field of study / Major"},
            {"selector": "#grad", "aria_label": "Expected graduation date"},
            {"selector": "#education", "label": "Education background"},
            {"selector": "#work", "label": "Work experience"},
            {"selector": "#internship", "label": "Internship experience"},
            {"selector": "#projects", "label": "Project experience"},
            {"selector": "#intro", "label": "Self introduction"},
            {"selector": "#why", "label": "Why do you want to join us?"},
        ]
    )

    assert [match.canonical_field for match in matches] == [
        "school",
        "degree",
        "major",
        "graduation_date",
        "education",
        "work_experience",
        "internships",
        "projects",
        "self_introduction",
        "open_question",
    ]
    assert all(not match.needs_user_confirmation for match in matches[:8])
    assert [match.reason for match in matches[-2:]] == [
        "answer_generator_required",
        "answer_generator_required",
    ]


def test_generated_open_answer_is_added_only_after_explicit_per_field_review():
    planner = FormPlanner()
    plan = planner.build_plan(
        PageSnapshot.from_mapping(
            {
                "page_url": "https://careers.example.com/apply",
                "controls": [
                    {
                        "selector": "#why",
                        "tag": "textarea",
                        "label": "Why do you want to join us?",
                        "required": True,
                    }
                ],
            }
        ),
        {},
    )
    item = plan.needs_user_confirmation[0]
    assert item.reason == "answer_generator_required"
    assert item.prompt == "Why do you want to join us?"
    with pytest.raises(ValueError, match="explicit review"):
        planner.add_confirmed_generated_answers(
            plan,
            {"#why": "Evidence-based generated answer"},
            confirmed_selectors={"#why"},
            review_confirmed=False,
        )

    approved = planner.add_confirmed_generated_answers(
        plan,
        {"#why": "Evidence-based generated answer"},
        confirmed_selectors={"#why"},
        review_confirmed=True,
    )
    assert approved.needs_user_confirmation == ()
    assert approved.actions[0].field_name == "open_question"
    assert "Evidence-based generated answer" not in repr(approved)


def test_captcha_and_login_detection_stop_fill_plan():
    snapshot = PageSnapshot.from_mapping(
        {
            "page_url": "https://careers.example.com/apply/1",
            "page_text": "Sign in to apply and verify you are human",
            "frame_urls": ["https://www.google.com/recaptcha/api2/anchor"],
            "controls": [
                {"selector": "#password", "type": "password", "visible": True},
                {"selector": "#email", "type": "email", "label": "Email"},
            ],
        }
    )

    blockers = detect_page_blockers(snapshot)
    plan = FormPlanner().build_plan(snapshot, {"email": "person@example.com"})

    assert {blocker.code for blocker in blockers} == {"captcha", "login_required"}
    assert plan.actions == ()
    assert {blocker.code for blocker in plan.blockers} == {"captcha", "login_required"}


def test_application_review_contains_no_values_and_requires_explicit_confirmation():
    snapshot = PageSnapshot.from_mapping(
        {
            "page_url": "https://careers.example.com/apply?token=secret",
            "controls": [{"selector": "#email", "type": "email", "label": "Email"}],
        }
    )
    plan = FormPlanner().build_plan(snapshot, {"email": "person@example.com"})
    review = ApplicationReview.from_plan(plan)

    assert review.review_confirmed is False
    assert "person@example.com" not in repr(plan)
    assert "person@example.com" not in repr(review)
    assert "person@example.com" not in str(review.public_summary())
    assert "secret" not in review.public_summary()["page_url"]
    assert review.confirm().review_confirmed is True


def test_submit_gate_is_off_by_default_and_never_allows_real_site():
    mock = DemoFormMock()
    values = {
        "first_name": "Test",
        "last_name": "Candidate",
        "email": "person@example.com",
        "resume": "C:/private/resume.docx",
    }
    review = mock.build_review(values)

    assert AUTO_SUBMIT is False
    assert evaluate_submit_gate(review.confirm()).reason == "auto_submit_disabled"
    assert evaluate_submit_gate(review, auto_submit=True).reason == "review_not_confirmed"
    with pytest.raises(DemoSubmissionBlocked, match="auto_submit_disabled"):
        mock.submit(review.confirm())

    receipt = mock.submit(review.confirm(), auto_submit=True)
    assert receipt.submitted is True
    assert receipt.destination == "local_demo_only"

    real_review = ApplicationReview(
        page_url="https://careers.example.com/apply",
        field_names=("email",),
        pending_confirmations=(),
        blockers=(),
        environment="real",
        review_confirmed=True,
    )
    assert evaluate_submit_gate(real_review, auto_submit=True).reason == "real_site_never_auto_submits"


def test_submit_gate_rejects_demo_label_on_non_local_target():
    review = ApplicationReview(
        page_url="https://careers.example.com/apply",
        field_names=("email",),
        pending_confirmations=(),
        blockers=(),
        environment="demo",
        review_confirmed=True,
    )
    decision = evaluate_submit_gate(review, auto_submit=True)
    assert decision.allowed is False
    assert decision.reason == "demo_target_must_be_local"


def test_form_filler_cannot_submit_real_page_with_spoofed_demo_review():
    review = ApplicationReview(
        page_url="http://127.0.0.1:8766/demo/apply",
        field_names=(),
        pending_confirmations=(),
        blockers=(),
        environment="demo",
        review_confirmed=True,
    )

    class FakeRealPage:
        url = "https://careers.example.com/apply"

        def locator(self, _selector: str):
            pytest.fail("a real page must be rejected before locating a submit button")

    with pytest.raises(
        DemoSubmitBlocked, match="browser_page_is_not_the_reviewed_local_demo"
    ):
        FormFiller().submit_demo(FakeRealPage(), review, auto_submit=True)


def test_generic_adapter_validates_urls_and_builds_apply_discovery_plan():
    adapter = GenericAdapter()
    with pytest.raises(UnsafeCareerUrl):
        adapter.validate_url("http://careers.example.com/jobs/1")
    with pytest.raises(UnsafeCareerUrl):
        adapter.validate_url("https://user:secret@careers.example.com/jobs/1")
    with pytest.raises(UnsafeCareerUrl):
        adapter.validate_url("https://127.0.0.1/jobs/1")

    plan = adapter.plan_apply_discovery(
        "https://careers.example.com/jobs/1",
        [
            {"href": "/apply/1", "text": "Apply now", "visible": True},
            {"href": "https://ats.example.net/application/1", "aria_label": "Apply"},
            {"href": "javascript:alert(1)", "text": "Apply"},
            {"href": "/privacy", "text": "Privacy"},
        ],
    )

    assert len(plan.candidates) == 2
    assert plan.preferred_candidate.url == "https://careers.example.com/apply/1"
    assert plan.preferred_candidate.needs_user_confirmation is False
    assert plan.candidates[1].needs_user_confirmation is True
    assert plan.rejected_count == 1


def test_generic_adapter_local_demo_cannot_escape_to_public_internet():
    adapter = GenericAdapter(allow_local_demo=True)
    plan = adapter.plan_apply_discovery(
        "http://127.0.0.1:8766/demo/jobs/1",
        [
            {"href": "/demo/apply", "text": "Apply"},
            {"href": "https://careers.example.com/apply", "text": "Apply"},
        ],
    )
    assert [candidate.url for candidate in plan.candidates] == [
        "http://127.0.0.1:8766/demo/apply"
    ]
    assert plan.rejected_count == 1


def test_error_and_log_metadata_are_redacted():
    metadata = sanitize_log_metadata(
        {
            "email": "person@example.com",
            "phone": "13800000000",
            "page_url": "https://careers.example.com/apply?token=abc&job=1",
            "resume_path": "C:/Users/private/resume.docx",
            "message": "contact person@example.com or 13800000000",
        }
    )
    artifact = sanitized_error_artifact(
        page_url="https://careers.example.com/apply?token=abc",
        screenshot_path="C:/Users/private/error-person@example.com.png",
        error=RuntimeError("candidate person@example.com failed"),
    )

    assert metadata["email"] == "[REDACTED]"
    assert metadata["phone"] == "[REDACTED]"
    assert "abc" not in metadata["page_url"]
    assert metadata["resume_path"] == "[REDACTED]"
    assert "person@example.com" not in metadata["message"]
    assert "person@example.com" not in artifact["error_message"]
    assert "person@example.com" not in str(artifact["screenshot_name"])
    assert "C:/Users/private" not in str(artifact)


def test_browser_error_screenshot_masks_editable_controls_and_redacts_metadata(tmp_path):
    captured: dict[str, object] = {}
    mask_locator = object()

    class FakePage:
        url = "https://careers.example.com/apply?email=person@example.com"

        def locator(self, selector: str):
            captured["selector"] = selector
            return mask_locator

        def screenshot(self, **kwargs: object):
            captured.update(kwargs)
            Path(str(kwargs["path"])).write_bytes(b"masked-demo-image")

    agent = BrowserAgent(headless=True, screenshot_dir=tmp_path)
    agent.page = FakePage()
    metadata = agent._capture_error(RuntimeError("failed for person@example.com"))

    assert captured["mask"] == [mask_locator]
    assert captured["mask_color"] == "#000000"
    assert metadata["screenshot_recorded"] is True
    assert "person@example.com" not in str(metadata)
