from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .autofill import AutofillPlan, create_autofill_plan
from .autofill_runner import start_autofill_process
from .models import MatchResult
from .profile import (
    DEFAULT_RESUMES_DIR,
    ApplicantProfile,
    select_resume,
    store_resume,
)
from .resume import ResumeError, read_resume_file
from .resume_export import ResumeExportError, resume_text_to_docx
from .storage import ApplicationRepository
from .tailoring import TailoringError, tailor_resume


class OneClickApplyError(RuntimeError):
    """The selected job cannot enter the controlled one-click flow."""


@dataclass(frozen=True, slots=True)
class OneClickApplyResult:
    process: Any
    plan: AutofillPlan
    resume_path: Path
    resume_language: str
    tailored: bool
    reused_tailored: bool
    note: str


def _record_resume(record: dict[str, object], language: str) -> Path | None:
    path_text = str(record.get("resume_path") or "").strip()
    if not path_text or str(record.get("resume_language") or "").casefold() != language:
        return None
    try:
        path = Path(path_text).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return path if path.is_file() else None


def _advance_to_opened(
    repository: ApplicationRepository, source: str, job_id: str
) -> None:
    current = repository.get(source, job_id)
    if current is None:
        raise OneClickApplyError("投递记录不存在")
    status = str(current["status"])
    if status == "review":
        repository.update_status(source, job_id, "confirmed")
        status = "confirmed"
    elif status == "blocked":
        repository.update_status(source, job_id, "confirmed")
        status = "confirmed"
    if status == "confirmed":
        repository.update_status(source, job_id, "opened")


def prepare_and_start_one_click(
    repository: ApplicationRepository,
    profile: ApplicantProfile,
    result: MatchResult,
    *,
    planned_language: str,
    company_foreign: bool,
    resumes_dir: str | Path = DEFAULT_RESUMES_DIR,
    process_starter: Callable[[AutofillPlan], Any] | None = None,
) -> OneClickApplyResult:
    """Prepare one truthful per-job resume and open one visible application form.

    Calling this function represents consent for exactly ``result.job``.  It never
    marks an application submitted; the headed runner only fills allow-listed
    fields and leaves the employer site's final submission to the applicant.
    """

    language = str(planned_language or "").strip().casefold()
    if language not in {"zh", "en"}:
        raise OneClickApplyError("简历语言必须是中文或英文")

    source = result.job.source
    job_id = result.job.id
    record = repository.get(source, job_id)
    if record is None:
        raise OneClickApplyError("投递记录不存在，请重新运行岗位匹配")
    if not bool(record.get("eligible")) or not result.eligible:
        raise OneClickApplyError("该岗位尚未达到你设置的匹配条件")
    status = str(record.get("status") or "")
    if status not in {"review", "confirmed", "opened", "blocked"}:
        raise OneClickApplyError("该岗位当前状态不能再次启动投递")

    existing = _record_resume(record, language)
    reused_tailored = bool(existing and record.get("resume_tailored"))
    tailored = reused_tailored
    note = "已复用这个岗位现有的定制简历。" if reused_tailored else ""

    if reused_tailored:
        resume_path = existing
    else:
        # The final route, not the most recently matched session text, decides
        # which source resume is safe to tailor and upload.
        resume_path = select_resume(
            profile,
            language,
            company_foreign=company_foreign or language == "en",
            resumes_dir=resumes_dir,
        )

        # Validate the destination, applicant profile and base file before any
        # derived resume is written locally.
        create_autofill_plan(
            result.job.url,
            profile,
            resume_path,
            source=source,
            consent=True,
            dry_run=False,
        )

        description = str(result.job.description or "").strip()
        if description:
            try:
                base_text = read_resume_file(resume_path)
                draft = tailor_resume(
                    base_text,
                    description,
                    job_title=result.job.title,
                    company=result.job.company,
                )
                payload = resume_text_to_docx(draft.tailored_text)
                resume_path = store_resume(
                    f"{result.job.company}_{result.job.title}_tailored.docx",
                    payload,
                    language,
                    resumes_dir=resumes_dir,
                )
                repository.approve_tailored_resume(
                    source,
                    job_id,
                    language,
                    resume_path,
                    changes="；".join(change.detail for change in draft.changes),
                    gaps="；".join(draft.gaps),
                    company_foreign=company_foreign,
                )
                tailored = True
                note = "已自动生成并使用只重排原有事实的岗位定制简历。"
            except (ResumeError, ResumeExportError, TailoringError, OSError):
                # Resume tailoring is an enhancement, not a reason to prevent a
                # user-confirmed application.  The validated base resume remains
                # the conservative fallback.
                tailored = False
                note = "定制简历未能生成，本次已安全改用对应语言的基准简历。"

        if not tailored:
            repository.set_resume_route(
                source,
                job_id,
                language,
                resume_path,
                company_foreign=company_foreign,
            )

    plan = create_autofill_plan(
        result.job.url,
        profile,
        resume_path,
        source=source,
        consent=True,
        dry_run=False,
    )

    # A click confirms one job.  If browser startup fails, leaving the record at
    # ``confirmed`` makes the failure visible and safely retryable.
    if status in {"review", "blocked"}:
        repository.update_status(source, job_id, "confirmed")
    starter = process_starter or start_autofill_process
    process = starter(plan)
    _advance_to_opened(repository, source, job_id)
    fresh = repository.get(source, job_id) or {}
    repository.update_details(
        source,
        job_id,
        notes=str(fresh.get("notes") or ""),
        next_action="在已打开的官网核对特殊问题并由本人点击最终提交",
        current_stage="基本资料与简历正在自动填写，待官网最终确认",
    )
    return OneClickApplyResult(
        process=process,
        plan=plan,
        resume_path=Path(resume_path),
        resume_language=language,
        tailored=tailored,
        reused_tailored=reused_tailored,
        note=note,
    )


__all__ = [
    "OneClickApplyError",
    "OneClickApplyResult",
    "prepare_and_start_one_click",
]
