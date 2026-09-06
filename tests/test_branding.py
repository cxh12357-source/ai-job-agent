from pathlib import Path

from branding import APP_NAME


def test_application_name():
    assert APP_NAME == "Charles-ai-job-agent"


def test_local_demo_page_displays_application_name():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ai_job_agent/browser/demo_form.html").read_text(encoding="utf-8")
    assert f"<title>{APP_NAME} Local Demo</title>" in source
