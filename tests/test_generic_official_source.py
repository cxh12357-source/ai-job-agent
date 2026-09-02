from __future__ import annotations

import json

import pytest

from ai_job_agent.sources.generic_official import (
    FetchResponse,
    GenericOfficialSource,
    SourceBlockedError,
    UnsafeCareerUrlError,
    discover_official_jobs,
)


BASE = "https://careers.example.com"
PUBLIC_IP = "93.184.216.34"


class FakeFetcher:
    def __init__(self, routes: dict[str, FetchResponse]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url: str) -> FetchResponse:
        self.calls.append(url)
        try:
            return self.routes[url]
        except KeyError as exc:
            raise AssertionError(f"unexpected network read: {url}") from exc


def response(
    url: str,
    text: str = "",
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
    history: tuple[str, ...] = (),
) -> FetchResponse:
    return FetchResponse(
        url=url,
        status_code=status,
        text=text,
        headers=headers or {"content-type": "text/html; charset=utf-8"},
        history=history,
    )


def public_resolver(_host: str, _port: int) -> tuple[str, ...]:
    return (PUBLIC_IP,)


def source(fetcher: FakeFetcher, **kwargs: object) -> GenericOfficialSource:
    return GenericOfficialSource(fetcher=fetcher, resolver=public_resolver, **kwargs)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://careers.example.com/jobs",
        "https://user:secret@careers.example.com/jobs",
        "https://careers.example.com:8443/jobs",
        "http://careers.example.com:443/jobs",
        "http://localhost/jobs",
        "http://app.localhost/jobs",
        "http://127.0.0.1/jobs",
        "http://10.2.3.4/jobs",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/jobs",
    ],
)
def test_rejects_non_public_or_non_web_urls_before_fetch(url: str) -> None:
    fetcher = FakeFetcher({})

    with pytest.raises(UnsafeCareerUrlError):
        source(fetcher).discover(url)

    assert fetcher.calls == []


def test_rejects_redirect_to_private_network_before_following_it() -> None:
    fetcher = FakeFetcher(
        {
            f"{BASE}/robots.txt": response(f"{BASE}/robots.txt", "User-agent: *\nAllow: /"),
            f"{BASE}/jobs": response(
                f"{BASE}/jobs",
                status=302,
                headers={"location": "http://127.0.0.1/internal"},
            ),
        }
    )

    with pytest.raises(UnsafeCareerUrlError):
        source(fetcher).discover(f"{BASE}/jobs")

    assert "http://127.0.0.1/internal" not in fetcher.calls


def test_rejects_redirect_hostname_that_resolves_to_private_network() -> None:
    private_target = "https://metadata.internal.example/jobs"
    fetcher = FakeFetcher(
        {
            f"{BASE}/robots.txt": response(f"{BASE}/robots.txt", "User-agent: *\nAllow: /"),
            f"{BASE}/jobs": response(
                f"{BASE}/jobs", status=302, headers={"location": private_target}
            ),
        }
    )

    def resolver(host: str, _port: int) -> tuple[str, ...]:
        return ("10.20.30.40",) if host == "metadata.internal.example" else (PUBLIC_IP,)

    with pytest.raises(UnsafeCareerUrlError):
        GenericOfficialSource(fetcher=fetcher, resolver=resolver).discover(f"{BASE}/jobs")

    assert private_target not in fetcher.calls


def test_accepts_benchmark_dns_only_after_fixed_public_probe_confirms_proxy() -> None:
    probe_calls = 0

    def proxied_resolver(host: str, _port: int) -> tuple[str, ...]:
        nonlocal probe_calls
        if host == "example.com":
            probe_calls += 1
            return ("198.19.2.88",)
        return ("198.19.1.181",)

    payload = {
        "@type": "JobPosting",
        "title": "Python Engineer",
        "hiringOrganization": {"name": "Example"},
        "url": f"{BASE}/jobs/1",
    }
    fetcher = FakeFetcher(
        {
            f"{BASE}/robots.txt": response(f"{BASE}/robots.txt", "User-agent: *\nAllow: /"),
            f"{BASE}/careers": response(
                f"{BASE}/careers",
                f"<script type='application/ld+json'>{json.dumps(payload)}</script>",
            ),
        }
    )

    result = GenericOfficialSource(
        fetcher=fetcher, resolver=proxied_resolver, max_pages=1
    ).discover(f"{BASE}/careers")

    assert [job.title for job in result.jobs] == ["Python Engineer"]
    assert probe_calls == 1


def test_rejects_benchmark_dns_when_public_probe_is_not_also_benchmark() -> None:
    def resolver(host: str, _port: int) -> tuple[str, ...]:
        return (PUBLIC_IP,) if host == "example.com" else ("198.19.1.181",)

    fetcher = FakeFetcher({})
    with pytest.raises(UnsafeCareerUrlError):
        GenericOfficialSource(fetcher=fetcher, resolver=resolver).discover(f"{BASE}/careers")

    assert fetcher.calls == []


@pytest.mark.parametrize(
    "probe_addresses",
    [
        ("198.19.2.88", "10.0.0.8"),
        ("198.19.2.88", "127.0.0.1"),
        ("198.19.2.88", "169.254.169.254"),
    ],
)
def test_mixed_unsafe_probe_answer_fails_closed(
    probe_addresses: tuple[str, ...],
) -> None:
    def resolver(host: str, _port: int) -> tuple[str, ...]:
        return probe_addresses if host == "example.com" else ("198.19.1.181",)

    fetcher = FakeFetcher({})
    with pytest.raises(UnsafeCareerUrlError):
        GenericOfficialSource(fetcher=fetcher, resolver=resolver).discover(f"{BASE}/careers")

    assert fetcher.calls == []


def test_proxy_mode_never_allows_literal_benchmark_or_private_dns() -> None:
    def proxied_resolver(host: str, _port: int) -> tuple[str, ...]:
        if host == "example.com":
            return ("198.19.2.88",)
        if host == "private.example.com":
            return ("10.1.2.3",)
        return ("198.19.1.181",)

    fetcher = FakeFetcher({})
    reader = GenericOfficialSource(fetcher=fetcher, resolver=proxied_resolver)

    with pytest.raises(UnsafeCareerUrlError):
        reader.discover("https://198.19.1.181/jobs")
    with pytest.raises(UnsafeCareerUrlError):
        reader.discover("https://private.example.com/jobs")

    assert fetcher.calls == []


def test_rejects_cross_origin_redirect_and_client_followed_private_history() -> None:
    cross_origin = FakeFetcher(
        {
            f"{BASE}/robots.txt": response(f"{BASE}/robots.txt", "User-agent: *\nAllow: /"),
            f"{BASE}/jobs": response(
                f"{BASE}/jobs",
                status=302,
                headers={"location": "https://other.example.org/jobs"},
            ),
        }
    )
    with pytest.raises(UnsafeCareerUrlError):
        source(cross_origin).discover(f"{BASE}/jobs")

    followed = FakeFetcher(
        {
            f"{BASE}/robots.txt": response(f"{BASE}/robots.txt", "User-agent: *\nAllow: /"),
            f"{BASE}/jobs": response(
                f"{BASE}/jobs",
                "<html></html>",
                history=("http://10.0.0.2/redirect",),
            ),
        }
    )
    with pytest.raises(UnsafeCareerUrlError):
        source(followed).discover(f"{BASE}/jobs")


def test_respects_robots_txt_without_reading_disallowed_page() -> None:
    fetcher = FakeFetcher(
        {
            f"{BASE}/robots.txt": response(
                f"{BASE}/robots.txt", "User-agent: *\nDisallow: /careers"
            )
        }
    )

    with pytest.raises(SourceBlockedError, match="robots.txt"):
        source(fetcher).discover(f"{BASE}/careers")

    assert fetcher.calls == [f"{BASE}/robots.txt"]


def test_rechecks_robots_before_following_same_origin_redirect() -> None:
    target = f"{BASE}/private/jobs"
    fetcher = FakeFetcher(
        {
            f"{BASE}/robots.txt": response(
                f"{BASE}/robots.txt", "User-agent: *\nDisallow: /private"
            ),
            f"{BASE}/careers": response(
                f"{BASE}/careers", status=302, headers={"location": target}
            ),
        }
    )

    with pytest.raises(SourceBlockedError, match="重定向"):
        source(fetcher).discover(f"{BASE}/careers")

    assert target not in fetcher.calls


def test_parses_jobposting_json_ld_into_domain_model() -> None:
    payload = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "identifier": {"value": "AI-42"},
        "title": "AI Agent Engineer",
        "description": "<p>Build safe <strong>Python</strong> agents.</p>",
        "qualifications": "Python and LLM experience",
        "responsibilities": "Ship reliable products",
        "employmentType": ["FULL_TIME", "PERMANENT"],
        "datePosted": "2026-08-30",
        "hiringOrganization": {"@type": "Organization", "name": "Example AI"},
        "jobLocation": {
            "@type": "Place",
            "address": {
                "addressLocality": "Shanghai",
                "addressRegion": "Shanghai",
                "addressCountry": "CN",
            },
        },
        "url": f"{BASE}/jobs/ai-42#apply",
    }
    html = (
        "<html><head><meta property='og:site_name' content='Fallback Corp'></head>"
        f"<body><script type='application/ld+json'>{json.dumps(payload)}</script></body></html>"
    )
    fetcher = FakeFetcher(
        {
            f"{BASE}/robots.txt": response(f"{BASE}/robots.txt", "User-agent: *\nAllow: /"),
            f"{BASE}/careers": response(f"{BASE}/careers", html),
        }
    )

    result = source(fetcher).discover(f"{BASE}/careers#openings")

    assert result.source_url == f"{BASE}/careers"
    assert result.pages_visited == (f"{BASE}/careers",)
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.job_id == "AI-42"
    assert job.title == "AI Agent Engineer"
    assert job.company == "Example AI"
    assert job.location == "Shanghai, CN"
    assert job.job_type == "FULL_TIME, PERMANENT"
    assert job.description == "Build safe Python agents."
    assert "Python and LLM experience" in str(job.requirements)
    assert job.job_url == f"{BASE}/jobs/ai-42"
    assert job.source == "Official website:careers.example.com"
    assert job.publish_date == "2026-08-30"


def test_discovers_semantic_cards_then_enriches_from_same_origin_detail() -> None:
    listing = """
    <html><head><meta property="og:site_name" content="Acme China"></head><body>
      <section class="job-list">
        <article class="job-card">
          <a href="/jobs/data-7"><h2>Data Analyst Intern</h2></a>
          <span class="location">深圳</span><span class="department">Data</span>
        </article>
        <article class="job-card">
          <a href="https://evil.example.org/jobs/trap"><h2>Security Engineer</h2></a>
        </article>
      </section>
    </body></html>
    """
    detail_payload = {
        "@type": "JobPosting",
        "identifier": "DATA-7",
        "title": "Data Analyst Intern",
        "description": "Analyse product data with Python and SQL.",
        "hiringOrganization": {"name": "Acme China"},
        "jobLocation": {"address": {"addressLocality": "Shenzhen", "addressCountry": "CN"}},
        "url": f"{BASE}/jobs/data-7",
    }
    detail = f"<script type='application/ld+json'>{json.dumps(detail_payload)}</script>"
    fetcher = FakeFetcher(
        {
            f"{BASE}/robots.txt": response(f"{BASE}/robots.txt", "User-agent: *\nAllow: /"),
            f"{BASE}/careers": response(f"{BASE}/careers", listing),
            f"{BASE}/jobs/data-7": response(f"{BASE}/jobs/data-7", detail),
        }
    )

    jobs = discover_official_jobs(
        f"{BASE}/careers",
        fetcher=fetcher,
        resolver=public_resolver,
        max_pages=4,
    )

    assert [job.job_id for job in jobs] == ["DATA-7"]
    assert jobs[0].description == "Analyse product data with Python and SQL."
    assert jobs[0].location == "Shenzhen, CN"
    assert "evil.example.org" not in " ".join(fetcher.calls)
    assert fetcher.calls.count(f"{BASE}/robots.txt") == 1


def test_recognises_opaque_early_career_card_but_not_campus_navigation() -> None:
    listing = """
    <html><head><meta property="og:site_name" content="Acme Campus"></head><body>
      <section class="campus-recruitment">
        <a href="/detail/graduate-7">2027届应届毕业生算法工程师</a>
        <a href="/campus/home">校园招聘</a>
      </section>
    </body></html>
    """
    fetcher = FakeFetcher(
        {
            f"{BASE}/robots.txt": response(f"{BASE}/robots.txt", "User-agent: *\nAllow: /"),
            f"{BASE}/campus": response(f"{BASE}/campus", listing),
            f"{BASE}/detail/graduate-7": response(
                f"{BASE}/detail/graduate-7",
                "<h1>2027届应届毕业生算法工程师</h1><main>面向应届毕业生</main>",
            ),
        }
    )

    result = source(fetcher, max_pages=3).discover(f"{BASE}/campus")

    assert [item.title for item in result.jobs] == ["2027届应届毕业生算法工程师"]
    assert f"{BASE}/campus/home" not in fetcher.calls


def test_honours_page_and_job_limits_without_unbounded_crawl() -> None:
    listing = """
    <div class="job-list">
      <a class="job" href="/jobs/1">Python Engineer</a>
      <a class="job" href="/jobs/2">Data Analyst</a>
      <a class="job" href="/jobs/3">Product Manager</a>
    </div>
    """
    fetcher = FakeFetcher(
        {
            f"{BASE}/robots.txt": response(f"{BASE}/robots.txt", "User-agent: *\nAllow: /"),
            f"{BASE}/careers": response(f"{BASE}/careers", listing),
        }
    )

    result = source(fetcher, max_pages=1, max_jobs=2).discover(f"{BASE}/careers")

    assert len(result.jobs) == 2
    assert result.pages_visited == (f"{BASE}/careers",)
    assert fetcher.calls == [f"{BASE}/robots.txt", f"{BASE}/careers"]


@pytest.mark.parametrize(
    "html",
    [
        "<html><head><title>Verify you are human</title></head><body></body></html>",
        "<html><body><iframe src='https://captcha.example/challenge'></iframe></body></html>",
        "<html><body><form><input type='password'></form></body></html>",
    ],
)
def test_pauses_on_captcha_or_login_instead_of_bypassing(html: str) -> None:
    fetcher = FakeFetcher(
        {
            f"{BASE}/robots.txt": response(f"{BASE}/robots.txt", "User-agent: *\nAllow: /"),
            f"{BASE}/careers": response(f"{BASE}/careers", html),
        }
    )

    with pytest.raises(SourceBlockedError, match="不会尝试绕过"):
        source(fetcher).discover(f"{BASE}/careers")


def test_meta_nofollow_keeps_current_jobs_but_does_not_visit_detail() -> None:
    html = """
    <html><head><meta name="robots" content="nofollow"></head><body>
      <div class="job-card"><a href="/jobs/1">Python Engineer</a></div>
    </body></html>
    """
    fetcher = FakeFetcher(
        {
            f"{BASE}/robots.txt": response(f"{BASE}/robots.txt", "User-agent: *\nAllow: /"),
            f"{BASE}/careers": response(f"{BASE}/careers", html),
        }
    )

    result = source(fetcher).discover(f"{BASE}/careers")

    assert len(result.jobs) == 1
    assert result.pages_visited == (f"{BASE}/careers",)
    assert any("nofollow" in warning for warning in result.warnings)
