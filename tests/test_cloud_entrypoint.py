from __future__ import annotations

import os
import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cloud_entrypoint_forces_safe_flags_before_loading_app() -> None:
    source = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")
    render_position = source.index("runpy.run_path")

    for assignment in (
        'os.environ["CLOUD_DEPLOYMENT"] = "true"',
        'os.environ["DEMO_MODE"] = "true"',
        'os.environ["AUTO_SUBMIT"] = "false"',
    ):
        assert source.index(assignment) < render_position


def test_cloud_entrypoint_executes_app_on_every_rerun(monkeypatch) -> None:
    execute_script = runpy.run_path
    calls: list[tuple[str, str]] = []
    for name in ("CLOUD_DEPLOYMENT", "DEMO_MODE", "AUTO_SUBMIT"):
        monkeypatch.setenv(name, "unset")

    def render_app(path: str, *, run_name: str) -> dict:
        calls.append((path, run_name))
        return {}

    monkeypatch.setattr(runpy, "run_path", render_app)
    for _ in range(2):
        execute_script(str(ROOT / "streamlit_app.py"), run_name="__test__")

    assert calls == [(str(ROOT / "app.py"), "__main__")] * 2
    assert os.environ["CLOUD_DEPLOYMENT"] == "true"
    assert os.environ["DEMO_MODE"] == "true"
    assert os.environ["AUTO_SUBMIT"] == "false"


def test_cloud_entrypoint_contains_no_credentials() -> None:
    source = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")

    assert "OPENAI_API_KEY" not in source
    assert "APP_ACCESS_PASSWORD" not in source
