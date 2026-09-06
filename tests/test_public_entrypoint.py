from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


def test_first_cloud_visit_opens_main_app_without_access_gate(
    monkeypatch, tmp_path: Path
) -> None:
    """Exercise the real Cloud entrypoint with no authenticated session."""
    monkeypatch.setenv("APP_ACCESS_PASSWORD", "synthetic-test-password")
    monkeypatch.setenv("AI_JOB_AGENT_DB", str(tmp_path / "app.db"))
    monkeypatch.setenv("AI_JOB_AGENT_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.setenv("AI_JOB_AGENT_LOGS", str(tmp_path / "logs"))

    app = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=20).run()

    assert not app.exception
    assert "访问密码" not in [field.label for field in app.text_input]
    assert "进入应用" not in [button.label for button in app.button]
    assert any(uploader.label == "上传个人 PDF 简历" for uploader in app.get("file_uploader"))


def test_active_entrypoints_do_not_reference_access_gate() -> None:
    combined = "\n".join(
        (ROOT / filename).read_text(encoding="utf-8")
        for filename in ("app.py", "streamlit_app.py")
    )

    for forbidden in (
        "require_access",
        "lock_current_session",
        "APP_ACCESS_PASSWORD",
        "访问密码",
        "进入应用",
        "只在你信任的设备和家庭网络中输入此密码",
    ):
        assert forbidden not in combined
