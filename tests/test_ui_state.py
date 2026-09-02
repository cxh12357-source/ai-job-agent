from __future__ import annotations

from ai_job_agent.ui_state import clear_job_search_state


def test_switching_source_group_discards_old_results_and_selection() -> None:
    state = {
        "aja_jobs": ["domestic-job"],
        "aja_assessments": {"domestic-job": {"match_score": 90}},
        "aja_selected": {"domestic-job"},
        "aja_search_report": {"company_scope": "domestic"},
        "aja_page_number": 3,
        "aja_profile": {"name": "Candidate"},
        "aja_notice": "queue message",
    }

    clear_job_search_state(state)

    assert state["aja_jobs"] == []
    assert state["aja_assessments"] == {}
    assert state["aja_selected"] == set()
    assert state["aja_search_report"] == {}
    assert state["aja_page_number"] == 1
    assert state["aja_profile"] == {"name": "Candidate"}
    assert state["aja_notice"] == "queue message"
