from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

import local_scraper as scraper


def config(**changes):
    return scraper.ScraperConfig.from_dict({
        "start_urls": ["https://careers.example.com/jobs/123"],
        "allowed_hosts": ["careers.example.com"],
        **changes,
    })


def job(**changes):
    return {
        "title": "Engineer", "company": "Example", "location": None,
        "description": "Use Python and CAD for mechanical engineering.",
        "job_url": "https://careers.example.com/jobs/123", **changes,
    }


@pytest.mark.parametrize("url", [
    "file:///C:/secret.txt", "javascript:alert(1)", "ftp://example.com/x",
    "https://user:password@example.com/job", "http://127.0.0.1/job", "http://10.0.0.5/job",
    "http://localhost/job", "http://169.254.169.254/latest/meta-data", "http://example.local/job",
    "https://careers.example.com:9222/job", "https://careers.example.com/?access_token=private",
    "https://careers.example.com/?email=person@example.com", "https://careers.example.com/a b",
    "https://careers.example.com\\@evil.com/job", "https://example.com:bad/job",
    "http://224.0.0.1/job", "https://[ff0e::1]/job", "https://@example.com/job",
    "https://careers.example.com/?apiKey=", "https://careers.example.com/?securityId=value",
    "https://careers.example.com/?session_id=value", "https://careers.example.com/?BearerToken=value",
])
def test_rejects_unsafe_job_urls(url):
    with pytest.raises(scraper.ScraperError):
        scraper.safe_url(url)


def test_job_url_preserves_non_sensitive_filters_and_removes_fragment():
    assert scraper.safe_url("https://careers.example.com/job?id=123#details") == "https://careers.example.com/job?id=123"


@pytest.mark.parametrize("changes", [
    {"start_urls": []}, {"allowed_hosts": ["*.example.com"]},
    {"allowed_hosts": ["other.example.com"]}, {"allowed_hosts": ["https://careers.example.com"]},
    {"max_pages": 11}, {"max_jobs": 0}, {"max_pages": True}, {"delay_seconds": 0},
    {"delay_seconds": float("inf")}, {"delay_seconds": float("nan")}, {"timeout_ms": 120000},
    {"selectors": {"apply_button": "button"}}, {"profile_dir": "C:/Users/me/Chrome"},
])
def test_configuration_enforces_host_and_bounded_limits(changes):
    with pytest.raises(scraper.ScraperError):
        config(**changes)


def test_configuration_merges_selectors_without_changing_browser_identity():
    value = config(selectors={"description": "#jd"})
    assert value.selectors["description"] == "#jd"
    assert value.selectors["title"] == "h1"
    assert value.delay_seconds == 3
    assert "user_agent" not in value.__dict__


def test_jsonld_extracts_graph_and_keeps_unknown_location_null():
    values = [{"@graph": [{"@type": "Organization"}, {
        "@type": ["Thing", "JobPosting"], "title": "Mechanical Engineer",
        "hiringOrganization": {"name": "Example Company"},
        "description": "<p>CAD skills.</p><script>secret()</script><p>Python.</p>",
        "qualifications": "Bachelor degree", "employmentType": ["FULL_TIME"],
        "datePosted": "2026-09-03",
    }]}]
    result = scraper.extract_jsonld(["bad json", json.dumps(values)], scraper.DEMO_JOB_URL)
    assert len(result) == 1
    assert result[0]["title"] == "Mechanical Engineer"
    assert result[0]["description"] == "CAD skills.\nPython."
    assert result[0]["location"] is None
    assert result[0]["requirements"] == "Bachelor degree"
    assert result[0]["publish_date"] == "2026-09-03"
    assert result[0]["job_type"] == "FULL_TIME"


def test_jsonld_extracts_location_without_inventing_company():
    posting = {"@type": "JobPosting", "title": "Engineer", "description": "CAD", "jobLocation": {
        "address": {"addressLocality": "Shanghai", "addressCountry": "CN"}}}
    assert scraper.extract_jsonld([json.dumps(posting)], scraper.DEMO_JOB_URL) == []
    found = scraper.extract_jsonld([json.dumps(posting)], scraper.DEMO_JOB_URL, "User specified company")
    assert found[0]["location"] == "Shanghai, CN"
    assert found[0]["company"] == "User specified company"


def test_normalization_whitelists_exported_fields():
    value = scraper.normalize_job(job(cookie="secret", password="secret", html="<form>personal</form>"), "")
    assert set(value) == {"title", "company", "description", "location", "job_url", "source"}
    assert scraper.normalize_job(job(description=""), "") is None
    assert scraper.normalize_job(job(job_url="https://example.com/?token=secret"), "") is None


@pytest.mark.parametrize("text,password,captcha,expected", [
    ("Please verify you are human", False, False, "captcha"),
    ("请完成滑块验证", False, False, "captcha"),
    ("Sign in to continue", False, False, "login"),
    ("", True, False, "login"), ("", False, True, "captcha"),
    ("Build CAPTCHA accessibility tooling", False, False, None),
    ("Mechanical engineer; Python preferred", False, False, None),
])
def test_challenge_detection(text, password, captcha, expected):
    assert scraper.detect_challenge(text, has_password=password, has_captcha=captcha) == expected


def test_detail_link_selection_is_explicit_and_bounded():
    links = [("one", "https://example.com/1"), ("two", "https://example.com/2")]
    assert scraper.select_links(links, 2, prompt=lambda _: "2, 1,2") == [links[1][1], links[0][1]]
    assert scraper.select_links(links, 1, prompt=lambda _: "") == []
    with pytest.raises(scraper.ScraperError):
        scraper.select_links(links, 1, prompt=lambda _: "1,2")
    with pytest.raises(scraper.ScraperError):
        scraper.select_links(links, 2, prompt=lambda _: "all")


def test_export_contract_deduplicates_and_never_overwrites(tmp_path):
    payload = scraper.export_payload([job(), job()], ["safe warning"])
    assert payload["kind"] == "charles-job-export"
    assert payload["schema_version"] == 1
    assert payload["source"] == "local-playwright"
    assert len(payload["jobs"]) == 1
    assert payload["exported_at"].endswith("+00:00")
    output = tmp_path / "jobs.json"
    scraper.write_export(output, payload)
    assert json.loads(output.read_text(encoding="utf-8")) == payload
    with pytest.raises(scraper.ScraperError):
        scraper.write_export(output, payload)
    with pytest.raises(scraper.ScraperError):
        scraper.write_export(tmp_path / "cookies.txt", payload)


class RobotsResponse:
    headers = {"Content-Type": "text/plain"}

    def __init__(self, body):
        self.body = body

    def read(self, limit):
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def policy_with_robots(monkeypatch, body=None, error=None):
    value = scraper.AccessPolicy(config())
    value.checked_hosts.add("careers.example.com")

    class Opener:
        def open(self, request, timeout):
            assert request.get_header("User-agent") == "CharlesJobAgent/1.0"
            assert "Cookie" not in request.headers
            if error:
                raise error
            return RobotsResponse(body)

    monkeypatch.setattr(scraper, "build_opener", lambda *args: Opener())
    return value


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_robots_http_errors_fail_closed(monkeypatch, status):
    error = HTTPError("https://careers.example.com/robots.txt", status, "restricted", {}, None)
    value = policy_with_robots(monkeypatch, error=error)
    with pytest.raises(scraper.ScraperError):
        value.require_allowed("https://careers.example.com/jobs/123")


def test_robots_network_errors_fail_closed(monkeypatch):
    value = policy_with_robots(monkeypatch, error=URLError("unavailable"))
    with pytest.raises(scraper.ScraperError):
        value.require_allowed("https://careers.example.com/jobs/123")


@pytest.mark.parametrize("body", [
    b"User-agent: *\nDisallow: /jobs/",
    b"User-agent: *\nAllow: /\nCrawl-delay: 10",
    b"User-agent: *\nAllow: /\nRequest-rate: 1/10",
])
def test_robots_restrictions_and_rate_limits_are_respected(monkeypatch, body):
    value = policy_with_robots(monkeypatch, body=body)
    with pytest.raises(scraper.ScraperError):
        value.require_allowed("https://careers.example.com/jobs/123")


def test_missing_robots_is_distinct_from_denied_robots(monkeypatch):
    error = HTTPError("https://careers.example.com/robots.txt", 404, "not found", {}, None)
    value = policy_with_robots(monkeypatch, error=error)
    value.require_allowed("https://careers.example.com/jobs/123")


def test_public_source_cannot_resolve_to_internal_network(monkeypatch):
    monkeypatch.setattr(scraper.socket, "getaddrinfo", lambda *args: [(2, 1, 6, "", ("10.0.0.5", 443))])
    with pytest.raises(scraper.ScraperError):
        scraper.AccessPolicy(config()).validate_url("https://careers.example.com/jobs/123")


def test_public_source_cannot_resolve_to_multicast_address(monkeypatch):
    monkeypatch.setattr(scraper.socket, "getaddrinfo", lambda *args: [(2, 1, 6, "", ("224.0.0.1", 443))])
    with pytest.raises(scraper.ScraperError):
        scraper.AccessPolicy(config()).validate_url("https://careers.example.com/jobs/123")


def test_demo_policy_blocks_every_external_origin_without_dns_or_robots(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Demo must not request DNS or public robots")
    monkeypatch.setattr(scraper.socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(scraper, "build_opener", forbidden)
    value = scraper.AccessPolicy(config(), demo_origin="http://127.0.0.1:12345")
    value.require_allowed("http://127.0.0.1:12345/jobs/demo")
    for url in ("https://example.com/", "http://127.0.0.1:12346/jobs", "http://10.0.0.1/"):
        with pytest.raises(scraper.ScraperError):
            value.validate_url(url)


def test_real_mode_requires_authorization_before_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(scraper, "run_browser", lambda *a, **kw: pytest.fail("Browser must not be started"))
    assert scraper.main(["--config", "missing.json", "--output", str(tmp_path / "out.json")]) == 2


def test_demo_refuses_external_configuration_before_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(scraper, "run_browser", lambda *a, **kw: pytest.fail("Browser must not be started"))
    assert scraper.main(["--demo", "--config", "external.json", "--output", str(tmp_path / "out.json")]) == 2


def test_real_chromium_demo_exports_job_and_never_contacts_external_source(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Demo must not make urllib/DNS network calls")
    monkeypatch.setattr(scraper.socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(scraper, "build_opener", forbidden)
    output = tmp_path / "browser-demo.json"
    assert scraper.main(["--demo", "--headless", "--output", str(output)]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["warnings"] == []
    assert len(payload["jobs"]) == 1
    assert payload["jobs"][0]["title"] == "Demo Mechanical Engineer"
    assert payload["jobs"][0]["job_url"] == scraper.DEMO_JOB_URL
    assert "127.0.0.1" not in output.read_text(encoding="utf-8")
    assert "cookie" not in output.read_text(encoding="utf-8").lower()


def test_bundled_json_example_matches_export_schema():
    example = Path(__file__).parents[1] / "examples" / "local_scraper_jobs.json"
    value = json.loads(example.read_text(encoding="utf-8"))
    assert value["jobs"] == scraper.export_payload(value["jobs"], [])["jobs"]
