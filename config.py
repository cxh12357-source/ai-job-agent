from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # The app still starts before optional dependencies are installed.
    load_dotenv = None


ROOT = Path(__file__).resolve().parent
if load_dotenv is not None:
    load_dotenv(ROOT / ".env", override=False)


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _project_path(name: str, default: str) -> Path:
    raw = os.getenv(name, default).strip() or default
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else ROOT / candidate


@dataclass(frozen=True, slots=True)
class AppSettings:
    demo_mode: bool
    auto_submit: bool
    cloud_deployment: bool
    openai_model: str
    openai_enabled: bool
    database_path: Path
    uploads_dir: Path
    logs_dir: Path
    access_password: str

    @classmethod
    def from_environment(cls) -> "AppSettings":
        api_key_present = bool(os.getenv("OPENAI_API_KEY", "").strip())
        return cls(
            demo_mode=_boolean("DEMO_MODE", True),
            auto_submit=_boolean("AUTO_SUBMIT", False),
            cloud_deployment=_boolean("CLOUD_DEPLOYMENT", False),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5-mini").strip()
            or "gpt-5-mini",
            openai_enabled=api_key_present,
            database_path=_project_path("AI_JOB_AGENT_DB", "data/ai_job_agent.db"),
            uploads_dir=_project_path("AI_JOB_AGENT_UPLOADS", "uploads"),
            logs_dir=_project_path("AI_JOB_AGENT_LOGS", "logs"),
            access_password=os.getenv("APP_ACCESS_PASSWORD", "").strip(),
        )

    def ensure_local_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)


SETTINGS = AppSettings.from_environment()


__all__ = ["AppSettings", "ROOT", "SETTINGS"]
