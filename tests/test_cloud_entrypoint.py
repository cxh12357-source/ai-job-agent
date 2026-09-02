from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cloud_entrypoint_forces_safe_flags_before_loading_app() -> None:
    source = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")
    import_position = source.index("from app import")

    for assignment in (
        'os.environ["CLOUD_DEPLOYMENT"] = "true"',
        'os.environ["DEMO_MODE"] = "true"',
        'os.environ["AUTO_SUBMIT"] = "false"',
    ):
        assert source.index(assignment) < import_position


def test_cloud_entrypoint_contains_no_credentials() -> None:
    source = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")

    assert "OPENAI_API_KEY" not in source
    assert "APP_ACCESS_PASSWORD" not in source
