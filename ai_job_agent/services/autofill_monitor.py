"""Run visible official-site autofill jobs and consume their public events."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from subprocess import Popen
from typing import Any, TextIO

from job_assistant.autofill import ALLOWED_PROFILE_FIELDS, AutofillPlan
from job_assistant.autofill_runner import start_autofill_process

from .application_service import ApplicationService


_EVENT_STATUSES = frozenset(
    {
        "application_form_opened",
        "browser_fallback",
        "blocked",
        "resuming",
        "manual_action_required",
        "ready_for_review",
        "error",
    }
)
_PUBLIC_FIELDS = frozenset({*ALLOWED_PROFILE_FIELDS, "resume"})
_ERROR_STAGE_LABELS = {
    "browser_launch": "启动可见浏览器",
    "open_job_page": "打开岗位页面",
    "read_job_page": "读取岗位页面",
    "wait_for_login_or_verification": "等待登录或验证",
    "open_application_form": "打开申请表单",
    "wait_for_application_verification": "等待申请页验证",
    "fill_known_fields": "填写已知资料",
    "wait_for_applicant_review": "等待本人核对",
}
_ERROR_CODES = frozenset(
    {
        "AutofillError",
        "Error",
        "OSError",
        "RuntimeError",
        "TimeoutError",
    }
)
_BROWSER_ENGINES = frozenset({"system-edge"})
_BROWSER_FALLBACK_MESSAGE = "内置浏览器不可用，已安全改用系统 Microsoft Edge"
_ACTIVE_QUEUE_IDS: set[int] = set()
_ACTIVE_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class AutofillEvent:
    status: str
    message: str
    filled_fields: tuple[str, ...] = ()
    blockers: tuple[dict[str, str], ...] = ()
    error_stage: str = ""
    error_code: str = ""
    browser_engine: str = ""


@dataclass(frozen=True, slots=True)
class AutofillLaunchRequest:
    queue_id: int
    plan: AutofillPlan


@dataclass(frozen=True, slots=True)
class AutofillBatchHandle:
    scheduled_queue_ids: tuple[int, ...]
    thread: threading.Thread | None


class AutofillLaunchError(RuntimeError):
    pass


def parse_autofill_event(line: str) -> AutofillEvent | None:
    """Parse only the runner's bounded, value-free public event schema."""

    try:
        payload = json.loads(str(line or ""))
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    status = str(payload.get("status") or "").strip()
    if status not in _EVENT_STATUSES:
        return None
    message = " ".join(str(payload.get("message") or "").split())[:300]

    raw_fields = payload.get("filled_fields")
    fields: list[str] = []
    if isinstance(raw_fields, list):
        for value in raw_fields:
            field = str(value or "").strip()
            if field in _PUBLIC_FIELDS and field not in fields:
                fields.append(field)

    blockers: list[dict[str, str]] = []
    raw_blockers = payload.get("blockers")
    if isinstance(raw_blockers, list):
        for value in raw_blockers[:30]:
            if not isinstance(value, Mapping):
                continue
            code = str(value.get("code") or "unknown").strip().casefold()
            code = re.sub(r"[^a-z0-9_]+", "_", code)[:80] or "unknown"
            blocker_message = " ".join(
                str(value.get("message") or "需要本人在官网补充").split()
            )[:300]
            blockers.append({"code": code, "message": blocker_message})

    # These diagnostic fields are emitted by our runner, but still cross a
    # subprocess boundary.  Accept exact known identifiers only: never persist
    # exception strings, page values, URLs, or attacker-controlled free text.
    raw_error_stage = str(payload.get("error_stage") or "").strip()
    error_stage = (
        raw_error_stage if raw_error_stage in _ERROR_STAGE_LABELS else ""
    )
    raw_error_code = str(payload.get("error_code") or "").strip()
    error_code = raw_error_code if raw_error_code in _ERROR_CODES else ""
    raw_browser_engine = str(payload.get("browser_engine") or "").strip()
    browser_engine = (
        raw_browser_engine if raw_browser_engine in _BROWSER_ENGINES else ""
    )
    return AutofillEvent(
        status=status,
        message=message,
        filled_fields=tuple(fields),
        blockers=tuple(blockers),
        error_stage=error_stage,
        error_code=error_code,
        browser_engine=browser_engine,
    )


def _confirmation_items(event: AutofillEvent) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for blocker in event.blockers:
        message = blocker["message"]
        field_name = message.rsplit("：", 1)[-1].strip() if "：" in message else message
        items.append(
            {
                "field_name": field_name[:120] or "官网留空项",
                "reason": message,
                "code": blocker["code"],
            }
        )
    if not items and event.status == "blocked":
        items.append(
            {
                "field_name": "官网验证或页面限制",
                "reason": event.message or "需要本人在官网处理后继续",
                "code": "blocked",
            }
        )
    return items


def _transition_if_possible(
    service: ApplicationService, queue_id: int, target: str
) -> None:
    current = service.get_queue_item(queue_id)
    if current is None or str(current["status"]) == target:
        return
    if str(current["status"]) in {"submitted", "skipped", "failed"}:
        return
    service.transition(queue_id, target)


def apply_autofill_event(
    service: ApplicationService, queue_id: int, event: AutofillEvent
) -> None:
    """Update one queue item from an actual visible-browser event."""

    current = service.get_queue_item(queue_id)
    if current is None or str(current["status"]) in {"submitted", "skipped"}:
        return
    if event.status == "error":
        if str(current["status"]) != "failed":
            stage_label = _ERROR_STAGE_LABELS.get(
                event.error_stage, "未知处理步骤"
            )
            code_suffix = f"（{event.error_code}）" if event.error_code else ""
            service.fail(
                queue_id,
                f"官网填写在“{stage_label}”阶段失败{code_suffix}，未提交任何申请",
            )
        return

    review = dict(current.get("review") or {})
    review.update(
        {
            "mode": "visible_official_browser",
            "last_browser_event": event.status,
            "browser_message": event.message,
            "will_submit": False,
            "unknown_fields_left_blank": True,
        }
    )
    if event.filled_fields:
        review["filled_fields"] = list(event.filled_fields)

    if event.status == "browser_fallback":
        review.update(
            {
                "browser_engine": event.browser_engine or "system-edge",
                # Do not store subprocess free text for this diagnostic event.
                "browser_message": _BROWSER_FALLBACK_MESSAGE,
            }
        )
        service.record_review(
            queue_id,
            review,
            needs_user_confirmation=list(
                current.get("needs_user_confirmation") or []
            ),
            required_field_missing=bool(current.get("required_field_missing")),
        )
        return

    if event.status == "application_form_opened":
        service.record_review(
            queue_id,
            review,
            needs_user_confirmation=list(current.get("needs_user_confirmation") or []),
            required_field_missing=bool(current.get("required_field_missing")),
        )
        return
    if event.status == "resuming":
        service.record_review(
            queue_id,
            review,
            needs_user_confirmation=[],
            required_field_missing=False,
        )
        _transition_if_possible(service, queue_id, "filling")
        return
    if event.status == "ready_for_review":
        service.record_review(
            queue_id,
            review,
            needs_user_confirmation=[],
            required_field_missing=False,
        )
        _transition_if_possible(service, queue_id, "ready_to_submit")
        return
    if event.status in {"blocked", "manual_action_required"}:
        needs = _confirmation_items(event)
        service.record_review(
            queue_id,
            review,
            needs_user_confirmation=needs,
            required_field_missing=bool(needs),
        )
        _transition_if_possible(service, queue_id, "waiting_user")


def _initial_review(request: AutofillLaunchRequest) -> dict[str, Any]:
    return {
        "mode": "visible_official_browser",
        "application_url": request.plan.public_summary()["application_url"],
        "fields_to_fill": list(request.plan.allowed_fields),
        "resume_selected": True,
        "automation_started": True,
        "unknown_fields_left_blank": True,
        "will_submit": False,
    }


def run_autofill_request(
    service: ApplicationService,
    request: AutofillLaunchRequest,
    *,
    process_starter: Callable[[AutofillPlan], Any] = start_autofill_process,
) -> None:
    """Run and monitor one visible browser; final Submit remains unavailable."""

    queue_id = int(request.queue_id)
    current = service.get_queue_item(queue_id)
    if current is None:
        raise AutofillLaunchError("投递队列记录不存在")
    status = str(current["status"])
    if status in {"pending", "waiting_user"}:
        service.transition(queue_id, "opening")
    elif status == "ready_to_submit":
        service.transition(queue_id, "filling")
    elif status != "opening":
        raise AutofillLaunchError("该岗位当前正在处理或已经结束")

    try:
        process = process_starter(request.plan)
        _transition_if_possible(service, queue_id, "filling")
        service.record_review(
            queue_id,
            _initial_review(request),
            needs_user_confirmation=[],
            required_field_missing=False,
        )

        stdout: TextIO | None = getattr(process, "stdout", None)
        if stdout is not None:
            for line in stdout:
                event = parse_autofill_event(line)
                if event is not None:
                    apply_autofill_event(service, queue_id, event)
        return_code = process.wait() if hasattr(process, "wait") else 0
        latest = service.get_queue_item(queue_id)
        latest_status = str(latest["status"]) if latest else ""
        if return_code not in {0, None} and latest_status not in {
            "waiting_user",
            "failed",
        }:
            service.fail(queue_id, "官网窗口提前关闭，未完成自动填写")
        elif return_code in {0, None} and latest_status == "filling":
            service.fail(queue_id, "官网窗口已关闭，但没有取得表单填写结果")
    except Exception:
        latest = service.get_queue_item(queue_id)
        if latest and str(latest["status"]) not in {"failed", "submitted", "skipped"}:
            service.fail(queue_id, "可见浏览器未能启动；没有提交任何申请")
        raise


def schedule_autofill_batch(
    service: ApplicationService,
    requests: Iterable[AutofillLaunchRequest],
    *,
    process_starter: Callable[[AutofillPlan], Any] = start_autofill_process,
) -> AutofillBatchHandle:
    """Open selected applications one at a time in a daemon worker."""

    unique: list[AutofillLaunchRequest] = []
    with _ACTIVE_LOCK:
        for request in requests:
            queue_id = int(request.queue_id)
            if queue_id in _ACTIVE_QUEUE_IDS:
                continue
            _ACTIVE_QUEUE_IDS.add(queue_id)
            unique.append(request)
    if not unique:
        return AutofillBatchHandle((), None)

    def worker() -> None:
        for request in unique:
            try:
                run_autofill_request(
                    service,
                    request,
                    process_starter=process_starter,
                )
            except Exception:
                # The queue item already contains a public failure; continue so
                # one website cannot block the rest of the selected batch.
                pass
            finally:
                with _ACTIVE_LOCK:
                    _ACTIVE_QUEUE_IDS.discard(int(request.queue_id))

    thread = threading.Thread(
        target=worker,
        name="ai-job-agent-visible-autofill",
        daemon=True,
    )
    thread.start()
    return AutofillBatchHandle(tuple(item.queue_id for item in unique), thread)


def is_autofill_scheduled(queue_id: int) -> bool:
    with _ACTIVE_LOCK:
        return int(queue_id) in _ACTIVE_QUEUE_IDS


__all__ = [
    "AutofillBatchHandle",
    "AutofillEvent",
    "AutofillLaunchError",
    "AutofillLaunchRequest",
    "apply_autofill_event",
    "is_autofill_scheduled",
    "parse_autofill_event",
    "run_autofill_request",
    "schedule_autofill_batch",
]
