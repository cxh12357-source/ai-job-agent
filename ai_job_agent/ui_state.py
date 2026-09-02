"""Small, framework-independent helpers for Streamlit session state."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any


def clear_job_search_state(state: MutableMapping[str, Any]) -> None:
    """Discard results when the user switches to a different source group."""

    state["aja_jobs"] = []
    state["aja_assessments"] = {}
    state["aja_selected"] = set()
    state["aja_search_report"] = {}
    state["aja_page_number"] = 1


def clear_resume_state(state: MutableMapping[str, Any], *, reset_upload: bool = False) -> None:
    """Forget only this session's active resume and its derived job matches."""

    state["aja_profile"] = None
    state["aja_resume_text"] = ""
    state["aja_resume_path"] = ""
    state["aja_resume_filename"] = ""
    state["aja_resume_size"] = 0
    state["aja_saved_profile_checked"] = True
    state["aja_notice"] = ""
    clear_job_search_state(state)
    if reset_upload:
        state["aja_resume_upload_revision"] = int(state.get("aja_resume_upload_revision", 0)) + 1


__all__ = ["clear_job_search_state", "clear_resume_state"]
