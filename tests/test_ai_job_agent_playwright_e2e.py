from __future__ import annotations

from pathlib import Path

from ai_job_agent.browser.agent import BrowserAgent
from ai_job_agent.browser.demo_server import DemoServer


def _resume(tmp_path: Path) -> Path:
    path = tmp_path / "resume.pdf"
    path.write_bytes(b"%PDF-1.4\n% local test resume\n")
    return path


def _values(resume: Path) -> dict[str, object]:
    return {
        "first_name": "Demo",
        "last_name": "Candidate",
        "email": "demo@example.test",
        "phone": "13800000000",
        "resume": str(resume),
    }


def test_playwright_fills_and_submits_only_confirmed_local_demo(tmp_path):
    resume = _resume(tmp_path)
    with DemoServer() as server, BrowserAgent(
        headless=True,
        allow_local_demo=True,
        screenshot_dir=tmp_path / "errors",
    ) as agent:
        result = agent.run_application(
            server.apply_url,
            _values(resume),
            environment="demo",
            review_confirmed=True,
            auto_submit=True,
        )

        assert result.status == "submitted_demo"
        assert result.submitted_demo is True
        assert set(result.filled_fields) == {
            "first_name",
            "last_name",
            "email",
            "phone",
            "resume",
        }
        assert agent.page.locator("#submission-status").inner_text() == "Demo submitted locally"


def test_playwright_real_environment_never_clicks_final_submit(tmp_path):
    resume = _resume(tmp_path)
    with DemoServer() as server, BrowserAgent(
        headless=True,
        allow_local_demo=True,
        screenshot_dir=tmp_path / "errors",
    ) as agent:
        result = agent.run_application(
            server.apply_url,
            _values(resume),
            environment="real",
            review_confirmed=True,
            auto_submit=True,
        )

        assert result.status == "ready_for_manual_submit"
        assert result.submitted_demo is False
        assert agent.page.locator("#submission-status").inner_text() == "Not submitted"


def test_playwright_discovers_apply_link_without_clicking(tmp_path):
    with DemoServer() as server, BrowserAgent(
        headless=True,
        allow_local_demo=True,
        screenshot_dir=tmp_path / "errors",
    ) as agent:
        plan = agent.discover_apply_links(server.job_url)

        assert plan.preferred_candidate is not None
        assert plan.preferred_candidate.url == server.apply_url
        assert agent.page.url == server.job_url
        assert agent.page.locator("h1").inner_text() == "Local Demo Engineer"


def test_playwright_enters_generic_job_apply_page_and_fills_without_submit(tmp_path):
    resume = _resume(tmp_path)
    with DemoServer() as server, BrowserAgent(
        headless=True,
        allow_local_demo=True,
        screenshot_dir=tmp_path / "errors",
    ) as agent:
        result = agent.run_from_job_page(
            server.job_url,
            _values(resume),
            environment="real",
            review_confirmed=True,
            auto_submit=True,
        )

        assert result.status == "ready_for_manual_submit"
        assert result.submitted_demo is False
        assert set(result.filled_fields) == {
            "first_name",
            "last_name",
            "email",
            "phone",
            "resume",
        }
        assert agent.page.url == server.apply_url
        assert agent.page.locator("#submission-status").inner_text() == "Not submitted"


def test_playwright_pauses_on_captcha_and_login(tmp_path):
    with DemoServer() as server, BrowserAgent(
        headless=True,
        allow_local_demo=True,
        screenshot_dir=tmp_path / "errors",
    ) as agent:
        captcha_result = agent.run_application(
            server.captcha_url,
            {},
            environment="demo",
        )
        assert captcha_result.status == "paused_for_user"
        assert [blocker.code for blocker in captcha_result.plan.blockers] == ["captcha"]

        login_result = agent.run_application(
            server.login_url,
            {},
            environment="demo",
        )
        assert login_result.status == "paused_for_user"
        assert [blocker.code for blocker in login_result.plan.blockers] == [
            "login_required"
        ]
