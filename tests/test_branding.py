from pathlib import Path

from streamlit.testing.v1 import AppTest

from branding import APP_NAME


def test_application_name():
    assert APP_NAME == "Charles-ai-job-agent"


def test_login_page_displays_application_name():
    app = AppTest.from_string(
        "from access_control import require_access\n"
        "require_access('test-access-password-only', required=True)\n"
    ).run()

    assert not app.exception
    assert app.title[0].value == APP_NAME
    assert len(app.text_input) == 1
    assert app.text_input[0].label == "访问密码"


def test_local_demo_page_displays_application_name():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ai_job_agent/browser/demo_form.html").read_text(encoding="utf-8")
    assert f"<title>{APP_NAME} Local Demo</title>" in source
