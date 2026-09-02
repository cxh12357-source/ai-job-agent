"""Resume upload processing without retaining personal files on cloud disk."""

from dataclasses import dataclass
from pathlib import Path

from ..models import CandidateProfile
from .profile_builder import build_candidate_profile
from .resume_parser import parse_resume, save_upload


@dataclass(frozen=True)
class ResumeIntakeResult:
    filename: str
    size_bytes: int
    text: str
    profile: CandidateProfile
    saved_path: str


def process_resume_upload(
    filename: str,
    content: bytes,
    *,
    cloud_mode: bool,
    uploads_dir: str | Path,
    use_openai: bool = False,
    model: str | None = None,
) -> ResumeIntakeResult:
    """Parse only supplied facts; cloud callers keep results in session state."""

    text = parse_resume(filename, content)
    profile = build_candidate_profile(text, use_openai=use_openai, model=model)
    path = "" if cloud_mode else str(save_upload(filename, content, uploads_dir=uploads_dir))
    return ResumeIntakeResult(
        filename=Path(filename.replace("\\", "/")).name,
        size_bytes=len(content),
        text=text,
        profile=profile,
        saved_path=path,
    )
