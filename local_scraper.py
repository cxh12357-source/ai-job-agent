"""User-operated local JD reader. No cloud browser, stealth, or application submission.

Run ``python local_scraper.py --demo --headless`` for a loopback-only browser test.
Only the standard library and Playwright are needed by this standalone file.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import socket
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser


ROOT = Path(__file__).resolve().parent
PROFILE_DIR = ROOT / "data" / "browser_profiles" / "local_scraper"
BOT_NAME = "CharlesJobAgent"
DEMO_JOB_URL = "https://careers.example.com/jobs/demo-engineer"
MAX_EXPORT_BYTES = 2_000_000
SENSITIVE_QUERY_KEYS = {
    "auth", "authorization", "apikey", "key", "token", "jwt", "bearer", "password", "passwd",
    "secret", "session", "sessionid", "sid", "sso", "cookie", "code", "state", "signature", "sig",
    "email", "phone", "mobile", "username", "userid", "user", "accesskey", "securityid", "ticket",
    "ticketid", "samlresponse", "samlrequest",
}
DEFAULT_SELECTORS = {
    "title": "h1",
    "company": '[itemprop="hiringOrganization"] [itemprop="name"], .company-name',
    "location": '[itemprop="jobLocation"], .job-location',
    "description": '[itemprop="description"], .job-description, #job-description',
    "requirements": ".job-requirements",
    "job_links": "",
}


class ScraperError(ValueError):
    """A safe, user-readable configuration or access error."""


def safe_url(value: str, *, allow_loopback: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\x00-\x20\\]", value):
        raise ScraperError("URL must be a normal HTTP(S) URL without whitespace.")
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as exc:
        raise ScraperError("Invalid URL.") from exc
    if parts.scheme not in {"http", "https"} or not host or parts.username is not None or parts.password is not None:
        raise ScraperError("Only HTTP(S) URLs without embedded credentials are supported.")
    if not allow_loopback and port not in {None, 80, 443}:
        raise ScraperError("Real job URLs must use standard HTTP(S) ports.")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
        if not allow_loopback:
            raise ScraperError("Private/local addresses are not accepted as real job sources.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if (address is not None and (not address.is_global or address.is_multicast or address.is_reserved)
            and not (allow_loopback and address.is_loopback)):
        raise ScraperError("Private/local addresses are not accepted as real job sources.")
    def sensitive(key: str) -> bool:
        key = key.replace("-", "").replace("_", "").casefold()
        return (key in SENSITIVE_QUERY_KEYS or any(part in key for part in
                ("token", "password", "secret", "credential", "signature", "cookie"))
                or key.endswith(("apikey", "sessionid", "authorization")))
    if any(sensitive(key) for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
        raise ScraperError("Remove authentication/personal-data query parameters from job URLs.")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


@dataclass
class ScraperConfig:
    start_urls: list[str]
    allowed_hosts: list[str]
    selectors: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SELECTORS))
    company: str | None = None
    max_pages: int = 5
    max_jobs: int = 5
    delay_seconds: float = 3.0
    timeout_ms: int = 20000

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ScraperConfig":
        if not isinstance(raw, dict):
            raise ScraperError("Configuration must be a JSON object.")
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise ScraperError("Unknown configuration keys: " + ", ".join(sorted(unknown)))
        urls, hosts = raw.get("start_urls"), raw.get("allowed_hosts")
        if not isinstance(urls, list) or not 1 <= len(urls) <= 10:
            raise ScraperError("Provide 1–10 exact start_urls.")
        if not isinstance(hosts, list) or not 1 <= len(hosts) <= 12:
            raise ScraperError("Provide 1–12 exact allowed_hosts; wildcard hosts are unsupported.")
        if any(not isinstance(h, str) or not re.fullmatch(r"[a-z0-9.-]+", h) for h in hosts):
            raise ScraperError("allowed_hosts must contain lowercase hostnames, not URLs or wildcards.")
        clean_urls = [safe_url(url) for url in urls]
        if any(urlsplit(url).hostname not in hosts for url in clean_urls):
            raise ScraperError("Every start URL must belong to allowed_hosts.")
        values = dict(raw, start_urls=list(dict.fromkeys(clean_urls)), allowed_hosts=list(dict.fromkeys(hosts)))
        for name, default, minimum, maximum in (
            ("max_pages", 5, 1, 10), ("max_jobs", 5, 1, 10), ("timeout_ms", 20000, 1000, 60000),
        ):
            value = raw.get(name, default)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ScraperError(f"{name} must be an integer from {minimum} to {maximum}.")
        delay = raw.get("delay_seconds", 3.0)
        if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not 3 <= delay <= 30:
            raise ScraperError("delay_seconds must be between 3 and 30.")
        selectors = raw.get("selectors", {})
        if not isinstance(selectors, dict) or set(selectors) - set(DEFAULT_SELECTORS):
            raise ScraperError("Unsupported selectors; use title/company/location/description/requirements/job_links.")
        if any(not isinstance(s, str) or len(s) > 500 for s in selectors.values()):
            raise ScraperError("Each CSS selector must be a string of at most 500 characters.")
        values["selectors"] = {**DEFAULT_SELECTORS, **selectors}
        if raw.get("company") is not None and (not isinstance(raw["company"], str) or len(raw["company"]) > 200):
            raise ScraperError("company must be a short factual company name or null.")
        return cls(**values)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in {"script", "style"}:
            self.skip += 1
        if tag in {"p", "br", "li", "div"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def clean_text(value: Any, limit: int = 20000) -> str:
    if not isinstance(value, str):
        return ""
    parser = _Text()
    parser.feed(value[:100000])
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())[:limit]


def normalize_job(raw: dict[str, Any], fallback_url: str) -> dict[str, Any] | None:
    title, company = clean_text(raw.get("title"), 200), clean_text(raw.get("company"), 200)
    description = clean_text(raw.get("description"))
    if not title or not company or not description:
        return None
    try:
        job_url = safe_url(raw.get("job_url") or fallback_url)
    except ScraperError:
        return None
    result: dict[str, Any] = {
        "title": title, "company": company, "location": clean_text(raw.get("location"), 300) or None,
        "description": description, "job_url": job_url, "source": "local-playwright",
    }
    for name in ("requirements", "job_type", "department", "publish_date"):
        value = clean_text(raw.get(name), 10000 if name == "requirements" else (100 if name == "publish_date" else 200))
        if value:
            result[name] = value
    return result


def _job_nodes(value: Any):
    if isinstance(value, list):
        for item in value:
            yield from _job_nodes(item)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        if types == "JobPosting" or (isinstance(types, list) and "JobPosting" in types):
            yield value
        if "@graph" in value:
            yield from _job_nodes(value["@graph"])


def extract_jsonld(blobs: list[str], fallback_url: str, company: str | None = None) -> list[dict[str, Any]]:
    jobs = []
    for blob in blobs[:50]:
        if len(blob) > 500000:
            continue
        try:
            value = json.loads(blob)
        except (ValueError, TypeError):
            continue
        for node in _job_nodes(value):
            organization = node.get("hiringOrganization", {})
            employer = organization.get("name") if isinstance(organization, dict) else organization
            locations = node.get("jobLocation", [])
            if isinstance(locations, dict):
                locations = [locations]
            places = []
            if isinstance(locations, list):
                for location in locations:
                    address = location.get("address", {}) if isinstance(location, dict) else {}
                    if isinstance(address, dict):
                        places.extend(address[key] for key in ("addressLocality", "addressRegion", "addressCountry")
                                      if isinstance(address.get(key), str))
                    elif isinstance(address, str):
                        places.append(address)
            job_type = node.get("employmentType")
            if isinstance(job_type, list):
                job_type = ", ".join(item for item in job_type if isinstance(item, str))
            job = normalize_job({
                "title": node.get("title"), "company": employer or company,
                "location": ", ".join(dict.fromkeys(places)) or ("Remote" if node.get("jobLocationType") == "TELECOMMUTE" else None),
                "description": node.get("description"), "job_url": node.get("url") or fallback_url,
                "requirements": "\n".join(clean_text(node.get(k)) for k in ("qualifications", "skills")),
                "job_type": job_type, "publish_date": node.get("datePosted"),
            }, fallback_url)
            if job:
                jobs.append(job)
    return jobs


def detect_challenge(text: str, *, has_password: bool = False, has_captcha: bool = False) -> str | None:
    lower = text.lower()
    if has_captcha or any(word in lower for word in (
        "verify you are human", "complete the captcha", "security verification", "人机验证", "滑块验证", "拼图验证", "请完成验证",
    )):
        return "captcha"
    if has_password or any(word in lower for word in ("请先登录", "登录后查看", "sign in to continue", "log in to continue", "扫码登录")):
        return "login"
    return None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any):
        return None


class AccessPolicy:
    """Exact-host allowlist and fail-closed public robots policy, without login cookies."""

    def __init__(self, config: ScraperConfig, *, demo_origin: str | None = None):
        self.config = config
        self.demo_origin = demo_origin
        self.cache: dict[str, RobotFileParser] = {}
        self.checked_hosts: set[str] = set()

    def validate_url(self, url: str) -> str:
        clean = safe_url(url, allow_loopback=self.demo_origin is not None)
        parts = urlsplit(clean)
        if self.demo_origin:
            if f"{parts.scheme}://{parts.netloc}" != self.demo_origin:
                raise ScraperError("Demo blocked a non-fixture network request.")
            return clean
        if parts.hostname not in self.config.allowed_hosts:
            raise ScraperError("Navigation blocked: host is not in allowed_hosts.")
        if parts.hostname not in self.checked_hosts:
            try:
                addresses = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
            except socket.gaierror as exc:
                raise ScraperError("Unable to resolve source hostname.") from exc
            resolved = [ipaddress.ip_address(address[4][0]) for address in addresses]
            if not resolved or any(not address.is_global or address.is_multicast or address.is_reserved for address in resolved):
                raise ScraperError("Source resolved to a private/local address; access blocked.")
            self.checked_hosts.add(parts.hostname)
        return clean

    def require_allowed(self, url: str) -> None:
        clean = self.validate_url(url)
        if self.demo_origin:
            return
        parts = urlsplit(clean)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.cache:
            parser = RobotFileParser(origin + "/robots.txt")
            request = Request(parser.url, headers={"User-Agent": BOT_NAME + "/1.0"})
            try:
                with build_opener(_NoRedirect).open(request, timeout=10) as response:
                    body = response.read(512001)
                    if len(body) > 512000:
                        raise ScraperError("robots.txt is too large; source access paused.")
                    if "html" in response.headers.get("Content-Type", "").lower():
                        raise ScraperError("robots.txt returned an HTML/login page; access paused.")
                    parser.parse(body.decode("utf-8", "replace").splitlines())
            except HTTPError as exc:
                if exc.code in {404, 410}:
                    parser.parse(["User-agent: *", "Allow: /"])
                else:
                    raise ScraperError(f"robots.txt returned HTTP {exc.code}; access stopped, not bypassed.") from exc
            except (URLError, TimeoutError, OSError) as exc:
                raise ScraperError("Cannot verify robots.txt; access stopped. Retry later or use an authorized API.") from exc
            self.cache[origin] = parser
        parser = self.cache[origin]
        if not parser.can_fetch(BOT_NAME, clean):
            raise ScraperError("robots.txt disallows this URL. Use a permitted source/API instead.")
        crawl_delay = parser.crawl_delay(BOT_NAME) or parser.crawl_delay("*") or 0
        rate = parser.request_rate(BOT_NAME) or parser.request_rate("*")
        if rate and rate.requests:
            crawl_delay = max(crawl_delay, rate.seconds / rate.requests)
        if crawl_delay > self.config.delay_seconds:
            raise ScraperError(f"robots.txt requires {crawl_delay}s between reads; increase delay_seconds (max 30) or stop.")


def select_links(links: list[tuple[str, str]], limit: int, *, prompt: Callable[[str], str] = input) -> list[str]:
    if not links or limit <= 0:
        return []
    print("Select detail pages to read; no application buttons will be clicked:")
    for index, (label, url) in enumerate(links[:30], 1):
        print(f"  {index}. {label[:100]} — {url}")
    value = prompt(f"Enter up to {limit} numbers separated by commas, or Enter to skip: ").strip()
    if not value:
        return []
    if not re.fullmatch(r"\d+(\s*,\s*\d+)*", value):
        raise ScraperError("Selection must be comma-separated numbers.")
    indices = list(dict.fromkeys(int(n) for n in value.split(",")))
    if len(indices) > limit or any(i < 1 or i > min(len(links), 30) for i in indices):
        raise ScraperError("Selection exceeds available pages or configured page limit.")
    return [links[i - 1][1] for i in indices]


def export_payload(jobs: list[dict[str, Any]], warnings: list[str]) -> dict[str, Any]:
    normalized: dict[str, dict[str, Any]] = {}
    for raw in jobs[:10]:
        job = normalize_job(raw, "")
        if job:
            normalized[job["job_url"]] = job
    return {
        "schema_version": 1, "kind": "charles-job-export",
        "exported_at": datetime.now(timezone.utc).isoformat(), "source": "local-playwright",
        "jobs": list(normalized.values()), "warnings": [clean_text(w, 400) for w in warnings[:30]],
    }


def write_export(path: Path, payload: dict[str, Any]) -> None:
    if path.suffix.lower() != ".json":
        raise ScraperError("Output must be a .json file.")
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(content.encode("utf-8")) > MAX_EXPORT_BYTES:
        raise ScraperError("Export exceeds the 2 MB safety limit.")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never silently replace a previous export; choose another --output name.
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(content + "\n")
    except FileExistsError as exc:
        raise ScraperError("Output already exists. Choose a new --output filename.") from exc


def run_browser(config: ScraperConfig, *, headless: bool = False, demo_origin: str | None = None,
                profile_dir: Path = PROFILE_DIR, prompt: Callable[[str], str] = input) -> dict[str, Any]:
    try:
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
    except ImportError as exc:
        raise ScraperError("Install dependencies: pip install playwright; then python -m playwright install chromium") from exc
    policy = AccessPolicy(config, demo_origin=demo_origin)
    warnings: list[str] = []
    jobs: list[dict[str, Any]] = []
    queue, visited = list(config.start_urls), set()
    block_reason: list[str] = []
    restricted_statuses: list[int] = []

    def route_request(route: Any) -> None:
        try:
            url = route.request.url
            if url.startswith(("data:", "blob:")):
                route.continue_()
                return
            policy.validate_url(url)
            if route.request.is_navigation_request():
                policy.require_allowed(url)
            route.continue_()
        except ScraperError as exc:
            if route.request.is_navigation_request():
                block_reason.append(str(exc))
            route.abort()

    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir), headless=headless,
                accept_downloads=False, service_workers="block",
            )
            try:
                context.route("**/*", route_request)
                context.on("response", lambda response: restricted_statuses.append(response.status)
                           if response.status in {403, 429} else None)
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(config.timeout_ms)
                last_read = 0.0
                while queue and len(visited) < config.max_pages and len(jobs) < config.max_jobs:
                    url = queue.pop(0)
                    if url in visited:
                        continue
                    try:
                        policy.require_allowed(url)
                        delay = config.delay_seconds - (time.monotonic() - last_read)
                        if delay > 0:
                            time.sleep(delay)
                        last_read = time.monotonic()
                        visited.add(url)
                        block_reason.clear()
                        restricted_statuses.clear()
                        response = page.goto(url, wait_until="domcontentloaded", timeout=config.timeout_ms)
                        if response and response.status in {401, 403, 429}:
                            raise ScraperError(f"HTTP {response.status}: access restricted; no retry/bypass will be attempted.")
                        if response and response.status >= 400:
                            raise ScraperError(f"Source returned HTTP {response.status}.")
                        # Bounded readiness, not random 'human imitation' or anti-detection.
                        page.wait_for_timeout(700)
                        for _ in range(5):
                            if restricted_statuses:
                                raise ScraperError(f"HTTP {restricted_statuses[0]}: a source request was restricted; reading stopped.")
                            policy.require_allowed(page.url)
                            body = page.locator("body").inner_text(timeout=config.timeout_ms)[:12000]
                            password = page.locator('input[type="password"]:visible').count() > 0
                            captcha = page.locator(
                                'iframe[src*="captcha"]:visible, iframe[title*="challenge"]:visible, '
                                '[id*="captcha"]:visible, [class*="captcha"]:visible, '
                                '[id*="geetest"]:visible, [class*="geetest"]:visible'
                            ).count() > 0
                            challenge = detect_challenge(body, has_password=password, has_captcha=captcha)
                            if not challenge:
                                break
                            if headless:
                                raise ScraperError(f"Manual {challenge} required. Run without --headless and complete it yourself.")
                            print("检测到人机验证，请在弹出的浏览器中手动完成验证，完成后点击继续。" if challenge == "captcha"
                                  else "请在本地浏览器中自行登录；脚本不读取或输入密码。")
                            if prompt("Type continue after completion, or press Enter to stop this page: ").strip().lower() != "continue":
                                raise ScraperError("User left this page paused; nothing submitted.")
                        else:
                            raise ScraperError("Verification is still present after 5 checks; source paused.")
                        policy.require_allowed(page.url)
                        if restricted_statuses:
                            raise ScraperError(f"HTTP {restricted_statuses[0]}: a source request was restricted; reading stopped.")
                        fallback = DEMO_JOB_URL if demo_origin else safe_url(page.url)
                        found = extract_jsonld(page.locator('script[type="application/ld+json"]').all_text_contents(), fallback, config.company)
                        if not found:
                            data: dict[str, Any] = {"job_url": fallback}
                            for name in ("title", "company", "location", "description", "requirements"):
                                selector = config.selectors[name]
                                locator = page.locator(selector).first if selector else None
                                data[name] = locator.inner_text(timeout=2000) if locator is not None and locator.count() else None
                            data["company"] = data.get("company") or config.company
                            job = normalize_job(data, fallback)
                            found = [job] if job else []
                        for job in found:
                            if not demo_origin:
                                policy.validate_url(job["job_url"])
                            if not any(previous["job_url"] == job["job_url"] for previous in jobs):
                                jobs.append(job)
                            if len(jobs) >= config.max_jobs:
                                break
                        remaining = config.max_pages - len(visited) - len(queue)
                        if config.selectors["job_links"] and remaining > 0 and len(jobs) < config.max_jobs:
                            links: dict[str, str] = {}
                            elements = page.locator(config.selectors["job_links"])
                            for index in range(min(elements.count(), 30)):
                                anchor = elements.nth(index)
                                href = anchor.get_attribute("href")
                                if not href:
                                    continue
                                try:
                                    target = policy.validate_url(urljoin(page.url, href))
                                except ScraperError:
                                    continue
                                if target not in visited and target not in queue:
                                    links[target] = clean_text(anchor.inner_text(), 100) or "Job detail"
                            queue.extend(select_links([(label, target) for target, label in links.items()], remaining, prompt=prompt))
                        if not found:
                            warnings.append("A visited page had no complete JD. Configure selectors; title, company and description are required.")
                    except (ScraperError, PlaywrightError) as exc:
                        # Do not print URLs, raw HTML, screenshots, credentials, or browser exception dumps.
                        message = str(exc) if isinstance(exc, ScraperError) else (block_reason[-1] if block_reason else "Browser read failed or timed out; inspect the source manually and adjust selectors.")
                        warnings.append(message)
                        print("Paused/skipped: " + message)
                        if "HTTP 403" in message or "HTTP 429" in message:
                            break
                return export_payload(jobs, warnings)
            finally:
                context.close()
    except PlaywrightError as exc:
        raise ScraperError("Chromium could not start/close. Install it with python -m playwright install chromium; close any other scraper using this profile.") from exc


DEMO_HTML = '''<!doctype html><html lang="en"><meta charset="utf-8"><title>Local JD fixture</title>
<h1>Demo Mechanical Engineer</h1><p class="company-name">Demo Example Company</p>
<p class="job-location">Shanghai</p><section class="job-description">Use CAD and Python to develop mechanical products. This is a local test, not a real job.</section>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting","title":"Demo Mechanical Engineer","hiringOrganization":{"name":"Demo Example Company"},"jobLocation":{"address":{"addressLocality":"Shanghai"}},"description":"Use CAD and Python to develop mechanical products. This is a local test, not a real job.","employmentType":"INTERN","url":"https://careers.example.com/jobs/demo-engineer"}</script></html>'''


@contextmanager
def demo_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            content = DEMO_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="JSON configuration for permitted real job pages")
    parser.add_argument("--authorized", action="store_true", help="Confirm that reading configured sources is permitted by their terms and your authorization")
    parser.add_argument("--demo", action="store_true", help="Read only an in-process loopback fixture, never external sites")
    parser.add_argument("--headless", action="store_true", help="Testing only; manual login/verification requires a visible browser")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "local_jobs.json")
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ScraperError("Output already exists. Choose a new --output filename; existing exports are not overwritten.")
        if args.demo and args.config:
            raise ScraperError("--demo cannot be combined with --config; it never reads external source configuration.")
        if args.demo:
            with demo_server() as origin, tempfile.TemporaryDirectory(prefix="charles-scraper-demo-") as profile:
                config = ScraperConfig(start_urls=[origin + "/jobs/demo"], allowed_hosts=["127.0.0.1"], max_pages=1, max_jobs=1)
                payload = run_browser(config, headless=args.headless, demo_origin=origin, profile_dir=Path(profile))
        else:
            if not args.authorized:
                raise ScraperError("Real reading requires --authorized after checking the website's terms and your permission. No browser was started.")
            if not args.config:
                raise ScraperError("Provide --config examples/local_scraper_config.json with your exact permitted job URLs.")
            if args.config.stat().st_size > 65536:
                raise ScraperError("Configuration exceeds 64 KB.")
            config = ScraperConfig.from_dict(json.loads(args.config.read_text(encoding="utf-8-sig")))
            payload = run_browser(config, headless=args.headless)
        write_export(args.output, payload)
        print(f"Exported {len(payload['jobs'])} job(s), {len(payload['warnings'])} warning(s) to {args.output.resolve()}")
        print("Upload only this JSON in the web app's local job import. Never upload data/browser_profiles or login cookies.")
        return 0 if payload["jobs"] else 2
    except (ScraperError, OSError, ValueError, EOFError) as exc:
        print("Stopped: " + (str(exc) if isinstance(exc, ScraperError) else "Unable to read configuration/write export, or terminal input is unavailable."), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
