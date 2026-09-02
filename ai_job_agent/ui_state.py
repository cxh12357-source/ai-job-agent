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


__all__ = ["clear_job_search_state"]
