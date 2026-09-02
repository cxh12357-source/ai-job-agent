from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

from streamlit.testing.v1 import AppTest

from ai_job_agent import ui
from ai_job_agent.services.local_job_import import local_job_export_template, parse_local_job_export
from ai_job_agent.services.local_scraper_bundle import BUNDLE_FILES, build_local_scraper_bundle


def test_download_bundle_contains_only_curated_source_files():
    with ZipFile(BytesIO(build_local_scraper_bundle())) as archive:
        assert set(archive.namelist()) == set(BUNDLE_FILES)
        assert all(not name.startswith(("data/", "uploads/", "output/", ".env")) for name in archive.namelist())
        assert b"launch_persistent_context" in archive.read("local_scraper.py")
        assert archive.read("requirements-local-scraper.txt").startswith(b"playwright")


def test_scraper_example_imports_using_web_contract():
    sample = Path(__file__).resolve().parents[1] / "examples/local_scraper_jobs.json"
    result = parse_local_job_export(sample.name, sample.read_bytes())
    assert len(result.jobs) == 1
    assert result.jobs[0].source == "local-playwright"
    assert result.jobs[0].job_url.startswith("https://")
    assert not result.warnings


def test_local_export_to_streamlit_matching_does_not_start_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(ui, "SETTINGS", replace(ui.SETTINGS, cloud_deployment=True, demo_mode=True, openai_enabled=False, database_path=tmp_path / "test.db"))
    payload = local_job_export_template().encode("utf-8")
    upload = SimpleNamespace(name="local_jobs.json", getvalue=lambda: payload)
    monkeypatch.setattr(ui.st, "file_uploader", lambda *args, **kwargs: upload)

    def forbidden_browser(*args, **kwargs):
        raise AssertionError("Cloud import must not start a local browser")

    monkeypatch.setattr(ui, "_run_local_demo_browser", forbidden_browser)
    script = (
        "from ai_job_agent.ui import _init_state, _search_step, _application_service\n"
        "from ai_job_agent.demo_data import demo_profile\n"
        "_init_state()\n"
        "_search_step(demo_profile(), _application_service())\n"
    )
    app = AppTest.from_string(script, default_timeout=15).run()
    app.radio[0].set_value("导入本地 Playwright 岗位").run()
    assert not app.exception
    next(button for button in app.button if button.label == "导入岗位并匹配").click().run()
    assert not app.exception
    assert len(app.session_state["aja_jobs"]) == 1
    assert len(app.session_state["aja_assessments"]) == 1
    assert app.session_state["aja_search_report"]["mode"] == "local-playwright"
