from dataclasses import replace
import json
from types import SimpleNamespace

import pymupdf
from streamlit.testing.v1 import AppTest

from ai_job_agent import ui


SCRIPT = "from ai_job_agent.ui import _init_state, _resume_step\n_init_state()\n_resume_step()"


def test_ui_has_personal_pdf_upload_and_safe_ai_default(monkeypatch, tmp_path):
    monkeypatch.setattr(ui, "SETTINGS", replace(ui.SETTINGS, cloud_deployment=True, openai_enabled=False, uploads_dir=tmp_path))
    app = AppTest.from_string(SCRIPT).run()
    assert not app.exception
    assert app.get("file_uploader")[0].proto.label == "上传个人 PDF 简历"
    assert app.checkbox[0].value is False
    assert app.checkbox[0].disabled is True
    assert any("Streamlit 云端" in item.value for item in app.caption)


def test_upload_parse_preview_and_clear_workflow(monkeypatch, tmp_path):
    monkeypatch.setattr(ui, "SETTINGS", replace(ui.SETTINGS, cloud_deployment=True, openai_enabled=False, uploads_dir=tmp_path))
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Demo Applicant\ndemo@example.com\nPython Data Analysis Experience")
        payload = document.tobytes()
    upload = SimpleNamespace(name="sample.pdf", getvalue=lambda: payload)
    monkeypatch.setattr(ui.st, "file_uploader", lambda *args, **kwargs: upload)
    app = AppTest.from_string(SCRIPT).run()
    next(button for button in app.button if button.label == "解析我的简历").click().run()
    assert not app.exception
    assert app.session_state["aja_profile"].email == "demo@example.com"
    assert app.session_state["aja_resume_path"] == ""
    assert any("已解析：sample.pdf" in item.value for item in app.success)
    assert any("demo@example.com" in item.value for item in app.text)
    assert list(tmp_path.iterdir()) == []
    next(button for button in app.button if button.label == "清除此会话简历").click().run()
    assert not app.exception
    assert app.session_state["aja_profile"] is None
    assert app.session_state["aja_resume_text"] == ""


def test_profile_download_contains_latest_user_edits(monkeypatch, tmp_path):
    monkeypatch.setattr(ui, "SETTINGS", replace(ui.SETTINGS, cloud_deployment=True, demo_mode=True, openai_enabled=False, uploads_dir=tmp_path))
    downloads = []

    def capture_download(label, *, data, **kwargs):
        if label == "下载解析档案 JSON":
            downloads.append(json.loads(data))
        return False

    monkeypatch.setattr(ui.st, "download_button", capture_download)
    script = SCRIPT + "\nfrom ai_job_agent.ui import _profile_from_state, _profile_step\nprofile = _profile_from_state()\nif profile is not None:\n    _profile_step(profile)\n"
    app = AppTest.from_string(script).run()
    next(button for button in app.button if button.label == "使用示例简历").click().run()
    next(field for field in app.text_input if field.label == "姓名").set_value("New Demo Name").run()
    assert not app.exception
    assert app.session_state["aja_profile"].name == "New Demo Name"
    assert downloads[-1]["name"] == "New Demo Name"
