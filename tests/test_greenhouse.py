import pytest

from job_assistant.sources.greenhouse import SourceError, _parse_jobs, extract_board_token


def test_extracts_token_from_official_urls():
    assert extract_board_token("https://boards.greenhouse.io/acme/jobs/123") == "acme"
    assert extract_board_token("https://job-boards.greenhouse.io/acme") == "acme"
    assert extract_board_token("acme-inc") == "acme-inc"


def test_rejects_non_greenhouse_url_to_prevent_arbitrary_fetches():
    with pytest.raises(SourceError, match="仅支持"):
        extract_board_token("https://evil.example/acme")


def test_parses_and_cleans_job_html():
    jobs = _parse_jobs(
        {
            "jobs": [
                {
                    "id": 123,
                    "title": "Python Engineer",
                    "location": {"name": "上海"},
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
                    "content": "&lt;p&gt;Build &amp;amp; test Python&lt;/p&gt;",
                    "departments": [{"name": "Engineering"}],
                }
            ]
        },
        "acme",
    )
    assert jobs[0].description == "Build & test Python"
    assert jobs[0].department == "Engineering"


def test_parses_salary_language_and_company_name():
    jobs = _parse_jobs(
        {
            "jobs": [
                {
                    "id": 456,
                    "title": "AI Engineer",
                    "location": {"name": "Remote"},
                    "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/456",
                    "content": "Annual salary USD 120,000-150,000 per year.",
                    "language": "en",
                }
            ]
        },
        "acme",
        "Acme Global",
    )
    assert jobs[0].company == "Acme Global"
    assert jobs[0].salary_min == 120_000
    assert jobs[0].salary_max == 150_000
    assert jobs[0].currency == "USD"
    assert jobs[0].period == "year"
    assert jobs[0].language == "en"


def test_rejects_malformed_greenhouse_payload():
    with pytest.raises(SourceError, match="数据结构"):
        _parse_jobs([], "acme")  # type: ignore[arg-type]
    with pytest.raises(SourceError, match="岗位列表"):
        _parse_jobs({"jobs": {}}, "acme")


def test_ignores_non_http_application_urls():
    jobs = _parse_jobs(
        {
            "jobs": [
                {
                    "id": 123,
                    "title": "Unsafe",
                    "location": {"name": "Remote"},
                    "absolute_url": "javascript:alert(1)",
                    "content": "Python",
                }
            ]
        },
        "acme",
    )
    assert jobs == []


def test_ignores_insecure_http_application_urls():
    jobs = _parse_jobs(
        {
            "jobs": [
                {
                    "id": 124,
                    "title": "Insecure",
                    "location": {"name": "Remote"},
                    "absolute_url": "http://job-boards.greenhouse.io/acme/jobs/124",
                    "content": "Python",
                }
            ]
        },
        "acme",
    )
    assert jobs == []
