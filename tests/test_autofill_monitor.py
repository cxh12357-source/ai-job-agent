from __future__ import annotations

import io
import json
from pathlib import Path

from ai_job_agent.models import JobPosting, MatchAssessment
from ai_job_agent.services.application_service import ApplicationService
from ai_job_agent.services.autofill_monitor import (
    AutofillLaunchRequest,
    apply_autofill_event,
    parse_autofill_event,
    run_autofill_request,
    schedule_autofill_batch,
)
from job_assistant.autofill import create_autofill_plan


PROFILE = {
    "first_name": "Test",
    "last_name": "Candidate",
    "full_name": "Test Candidate",
    "email": "candidate@example.com",
    "phone": "13800000000",
    "location": "Shanghai",
    "school": "Example University",
    "degree": "Bachelor",
    "major": "Engineering",
    "graduation_date": "2027-06",
}


class FakeProcess:
    def __init__(self, events: list[dict[str, object]], return_code: int = 0) -> None:
        self.stdout = io.StringIO(
            "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
        )
        self.return_code = return_code

    def wait(self) -> int:
        return self.return_code


def _request(
    tmp_path: Path,
    *,
    suffix: str = "one",
) -> tuple[ApplicationService, AutofillLaunchRequest]:
    service = ApplicationService(tmp_path / "applications.db", demo_mode=False)
    job = JobPosting(
        job_id=f"job-{suffix}",
        title=f"Graduate Engineer {suffix}",
        company="Example",
        location="Shanghai",
        job_url=f"https://careers.example.com/jobs/{suffix}",
        source="OfficialCareer:careers.example.com",
    )
    assessment = MatchAssessment(match_score=80, reason="test")
    queue = service.enqueue_job(job, assessment=assessment, resume_version="resume.pdf")
    resume = tmp_path / f"resume-{suffix}.pdf"
    resume.write_bytes(b"%PDF-1.4\nfixture")
    plan = create_autofill_plan(
        job.job_url,
        PROFILE,
        resume,
        source=str(job.source),
        consent=True,
        dry_run=False,
    )
    return service, AutofillLaunchRequest(int(queue["id"]), plan)


def test_runner_event_parser_keeps_only_public_field_names() -> None:
    event = parse_autofill_event(
        json.dumps(
            {
                "status": "manual_action_required",
                "message": "known public message",
                "filled_fields": ["first_name", "resume", "national_id"],
                "blockers": [
                    {
                        "code": "unsupported_required_question",
                        "message": "发现需要人工回答的必填项：Work authorization",
                    }
                ],
            }
        )
    )

    assert event is not None
    assert event.filled_fields == ("first_name", "resume")
    assert event.blockers[0]["code"] == "unsupported_required_question"
    assert parse_autofill_event("not-json") is None


def test_error_diagnostics_accept_only_exact_allowlisted_identifiers() -> None:
    valid = parse_autofill_event(
        json.dumps(
            {
                "status": "error",
                "message": "must never become the saved failure",
                "error_stage": "browser_launch",
                "error_code": "RuntimeError",
            }
        )
    )
    assert valid is not None
    assert valid.error_stage == "browser_launch"
    assert valid.error_code == "RuntimeError"

    rejected = parse_autofill_event(
        json.dumps(
            {
                "status": "error",
                "error_stage": "browser_launch; applicant@example.com",
                "error_code": "RuntimeError: private page value",
            }
        )
    )
    assert rejected is not None
    assert rejected.error_stage == ""
    assert rejected.error_code == ""


def test_browser_fallback_records_safe_engine_without_changing_status(
    tmp_path: Path,
) -> None:
    service, request = _request(tmp_path)
    before = service.get_queue_item(request.queue_id)
    assert before is not None
    event = parse_autofill_event(
        json.dumps(
            {
                "status": "browser_fallback",
                "message": "untrusted subprocess text with page@example.com",
                "browser_engine": "system-edge",
            }
        )
    )
    assert event is not None

    apply_autofill_event(service, request.queue_id, event)

    queue = service.get_queue_item(request.queue_id)
    assert queue is not None
    assert queue["status"] == before["status"] == "pending"
    assert queue["review"]["browser_engine"] == "system-edge"
    assert queue["review"]["last_browser_event"] == "browser_fallback"
    assert "page@example.com" not in queue["review"]["browser_message"]


def test_manual_event_records_only_actual_blank_field(tmp_path: Path) -> None:
    service, request = _request(tmp_path)
    process = FakeProcess(
        [
            {
                "status": "manual_action_required",
                "message": "已填写可安全识别的资料",
                "filled_fields": ["first_name", "email", "resume"],
                "blockers": [
                    {
                        "code": "unsupported_required_question",
                        "message": "发现需要人工回答的必填项：Work authorization",
                    }
                ],
                "will_submit": False,
            }
        ]
    )

    run_autofill_request(service, request, process_starter=lambda _plan: process)

    queue = service.get_queue_item(request.queue_id)
    assert queue is not None
    assert queue["status"] == "waiting_user"
    assert queue["review"]["filled_fields"] == ["first_name", "email", "resume"]
    assert [item["field_name"] for item in queue["needs_user_confirmation"]] == [
        "Work authorization"
    ]
    assert queue["review"]["will_submit"] is False


def test_ready_event_has_no_fake_manual_confirmation_and_never_submits(
    tmp_path: Path,
) -> None:
    service, request = _request(tmp_path)
    process = FakeProcess(
        [
            {
                "status": "ready_for_review",
                "message": "已填写允许的资料，请核对",
                "filled_fields": ["full_name", "email", "phone", "resume"],
                "will_submit": False,
            }
        ]
    )

    run_autofill_request(service, request, process_starter=lambda _plan: process)

    queue = service.get_queue_item(request.queue_id)
    assert queue is not None
    assert queue["status"] == "ready_to_submit"
    assert queue["status"] != "submitted"
    assert queue["needs_user_confirmation"] == []
    assert queue["required_field_missing"] is False


def test_login_block_then_resume_uses_actual_browser_events(tmp_path: Path) -> None:
    service, request = _request(tmp_path)
    process = FakeProcess(
        [
            {
                "status": "blocked",
                "message": "需要你在可见浏览器中完成登录",
                "blockers": [
                    {"code": "login_required", "message": "页面要求先登录"}
                ],
            },
            {"status": "resuming", "message": "登录已完成"},
            {
                "status": "ready_for_review",
                "message": "预填完成",
                "filled_fields": ["email", "resume"],
            },
        ]
    )

    run_autofill_request(service, request, process_starter=lambda _plan: process)

    queue = service.get_queue_item(request.queue_id)
    assert queue is not None
    assert queue["status"] == "ready_to_submit"
    assert queue["needs_user_confirmation"] == []
    assert queue["review"]["last_browser_event"] == "ready_for_review"


def test_error_event_marks_only_that_job_failed(tmp_path: Path) -> None:
    service, request = _request(tmp_path)
    process = FakeProcess(
        [
            {
                "status": "error",
                "message": "private applicant@example.com exception details",
                "error_stage": "open_application_form",
                "error_code": "TimeoutError",
            }
        ],
        return_code=2,
    )

    run_autofill_request(service, request, process_starter=lambda _plan: process)

    queue = service.get_queue_item(request.queue_id)
    assert queue is not None
    assert queue["status"] == "failed"
    assert queue["last_error"] == (
        "官网填写在“打开申请表单”阶段失败（TimeoutError），未提交任何申请"
    )
    assert "applicant@example.com" not in queue["last_error"]


def test_batch_opens_selected_jobs_sequentially(tmp_path: Path) -> None:
    service, first = _request(tmp_path, suffix="one")
    _, second = _request(tmp_path, suffix="two")
    # Both helpers point to the same SQLite path and therefore share the queue.
    opened: list[str] = []

    def starter(plan: object) -> FakeProcess:
        opened.append(str(getattr(plan, "application_url")))
        return FakeProcess(
            [
                {
                    "status": "ready_for_review",
                    "message": "预填完成",
                    "filled_fields": ["email", "resume"],
                }
            ]
        )

    handle = schedule_autofill_batch(
        service,
        [first, second],
        process_starter=starter,
    )
    assert handle.thread is not None
    handle.thread.join(timeout=5)

    assert opened == [first.plan.application_url, second.plan.application_url]
    assert service.get_queue_item(first.queue_id)["status"] == "ready_to_submit"
    assert service.get_queue_item(second.queue_id)["status"] == "ready_to_submit"
