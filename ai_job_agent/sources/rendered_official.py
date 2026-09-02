"""One-page rendered DOM fallback for JavaScript-heavy official career sites."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Any
from urllib.parse import urlsplit

from ai_job_agent.adapters.base import UnsafeCareerUrl, normalize_career_url
from ai_job_agent.models import JobPosting


class RenderedOfficialError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RenderedDiscoveryResult:
    jobs: tuple[JobPosting, ...]
    final_url: str
    warnings: tuple[str, ...] = ()


_JOB_PATH_MARKERS = (
    "/job/",
    "/jobs/",
    "/position/",
    "/positions/",
    "/vacancy/",
    "/recruit/",
    "jobid=",
    "job_id=",
    "positionid=",
    "position_id=",
)
_GENERIC_TEXT = frozenset(
    {
        "jobs",
        "job",
        "careers",
        "career",
        "职位",
        "招聘",
        "社会招聘",
        "校园招聘",
        "校招",
        "应届生招聘",
        "毕业生招聘",
        "查看职位",
        "全部职位",
        "更多职位",
        "learn more",
        "view jobs",
        "search jobs",
        "apply",
        "apply now",
        "立即申请",
    }
)
_BLOCKER_MARKERS = (
    "verify you are human",
    "complete the captcha",
    "security verification",
    "请完成验证码",
    "验证您是人类",
    "登录后查看职位",
)
_LOCATION_MARKERS = (
    "北京",
    "上海",
    "深圳",
    "广州",
    "杭州",
    "苏州",
    "南京",
    "武汉",
    "成都",
    "重庆",
    "西安",
    "合肥",
    "长沙",
    "天津",
    "香港",
    "新加坡",
)


def _clean(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _title(value: object) -> str:
    raw = str(value or "").strip()
    first_line = next((line.strip() for line in raw.splitlines() if line.strip()), raw)
    return _clean(first_line, 160)


def _infer_location(text: str) -> str | None:
    found = [marker for marker in _LOCATION_MARKERS if marker in text]
    if re.search(r"\bremote\b|远程", text, re.I):
        found.append("Remote")
    unique = list(dict.fromkeys(found))
    # A container mentioning many filter cities is not a job location.
    if len(unique) > 3:
        return None
    return " / ".join(unique) or None


def _infer_job_type(title: str, nearby: str) -> str | None:
    text = f"{title} {nearby}".casefold()
    if "实习" in text or re.search(r"\bintern(?:ship)?\b", text):
        return "Intern"
    if "兼职" in text or "part-time" in text:
        return "Part-time"
    if "合同" in text or "contract" in text:
        return "Contract"
    if "全职" in text or "full-time" in text:
        return "Full-time"
    return None


def _same_origin(left: str, right: str) -> bool:
    a, b = urlsplit(left), urlsplit(right)
    return (a.scheme.casefold(), a.hostname, a.port) == (
        b.scheme.casefold(),
        b.hostname,
        b.port,
    )


def _looks_like_job_link(url: str, title: str) -> bool:
    lowered_url = url.casefold()
    normalized_title = title.casefold()
    if normalized_title in _GENERIC_TEXT or not 2 <= len(title) <= 160:
        return False
    if any(marker in lowered_url for marker in _JOB_PATH_MARKERS):
        return True
    # Some self-built boards use opaque detail routes.  A card with a concrete
    # role-like title is acceptable when its nearby text explicitly says it is
    # a position; the caller still enforces same-origin navigation.
    return any(
        marker in normalized_title
        for marker in (
            "工程师",
            "实习",
            "应届",
            "毕业生",
            "管培生",
            "经理",
            "专员",
            "engineer",
            "intern",
            "new grad",
            "graduate",
            "trainee",
            "manager",
            "analyst",
        )
    )


def jobs_from_rendered_links(
    page_url: str,
    links: Sequence[Mapping[str, Any]],
    *,
    company: str,
    max_jobs: int = 100,
) -> tuple[JobPosting, ...]:
    jobs: list[JobPosting] = []
    seen: set[str] = set()
    source_host = (urlsplit(page_url).hostname or "").casefold()
    source = f"Official website:{source_host}"
    for link in links:
        if link.get("visible") is False:
            continue
        title = _title(link.get("title_text") or link.get("text") or link.get("aria_label"))
        href = _clean(link.get("href"), 4096)
        if not href or href in seen or not _same_origin(page_url, href):
            continue
        if not _looks_like_job_link(href, title):
            continue
        seen.add(href)
        nearby = _clean(link.get("nearby_text"), 2_000)
        job_id = f"official:{source_host}:{sha256(href.encode('utf-8')).hexdigest()[:20]}"
        jobs.append(
            JobPosting(
                id=job_id,
                job_id=job_id,
                title=title,
                company=company or source_host,
                location=_infer_location(title) or _infer_location(nearby),
                job_type=_infer_job_type(title, nearby),
                description=nearby if nearby != title else "",
                job_url=href,
                source=source,
            )
        )
        if len(jobs) >= max(1, min(int(max_jobs), 200)):
            break
    return tuple(jobs)


_RENDER_SCRIPT = r"""
() => {
  const visible = (element) => {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' &&
      (rect.width > 0 || rect.height > 0);
  };
  const siteName = document.querySelector('meta[property="og:site_name"]');
  return {
    page_text: String(document.body ? document.body.innerText : '').slice(0, 50000),
    company: String(siteName ? siteName.content : document.title || '').slice(0, 200),
    links: Array.from(document.querySelectorAll('a[href]')).map((element) => {
      const card = element.closest(
        '[class*="job" i], [class*="position" i], [class*="vacancy" i], li, article'
      );
      const heading = element.querySelector(
        'h1, h2, h3, h4, [class*="title" i], [class*="name" i]'
      );
      const rawText = String(element.innerText || element.textContent || '').trim();
      const firstLine = rawText.split(/\r?\n/).map((item) => item.trim()).find(Boolean) || '';
      return {
        href: String(element.href || '').slice(0, 4096),
        title_text: String(heading ? heading.innerText || heading.textContent || '' : firstLine).trim().slice(0, 160),
        text: rawText.slice(0, 240),
        aria_label: String(element.getAttribute('aria-label') || '').slice(0, 240),
        nearby_text: String(card ? card.innerText || card.textContent || '' : '').trim().slice(0, 2000),
        visible: visible(element)
      };
    })
  };
}
"""


def discover_rendered_jobs(
    url: str,
    *,
    company: str | None = None,
    max_jobs: int = 100,
    timeout_ms: int = 30_000,
) -> RenderedDiscoveryResult:
    """Render one public page, without clicking, logging in, or bypassing checks."""

    try:
        start_url = normalize_career_url(url)
    except UnsafeCareerUrl as exc:
        raise RenderedOfficialError(str(exc)) from exc
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - dependency is part of the app
        raise RenderedOfficialError("缺少 Playwright，无法读取动态招聘页面") from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()

        def safe_route(route: Any, request: Any) -> None:
            request_url = str(request.url)
            if request_url.startswith(("data:", "blob:", "about:")):
                route.continue_()
                return
            try:
                normalize_career_url(request_url)
            except UnsafeCareerUrl:
                route.abort()
            else:
                route.continue_()

        context.route("**/*", safe_route)
        page = context.new_page()
        page.set_default_timeout(int(timeout_ms))
        try:
            page.goto(start_url, wait_until="domcontentloaded")
            page.wait_for_timeout(2_000)
            page.evaluate("window.scrollTo(0, document.body ? document.body.scrollHeight : 0)")
            page.wait_for_timeout(1_000)
            final_url = normalize_career_url(page.url)
            if not _same_origin(start_url, final_url):
                raise RenderedOfficialError("招聘页面跳转到其他域名，已停止动态读取")
            raw = page.evaluate(_RENDER_SCRIPT)
            if not isinstance(raw, Mapping):
                raise RenderedOfficialError("动态招聘页面未返回可识别结构")
            page_text = _clean(raw.get("page_text"), 50_000).casefold()
            if any(marker in page_text for marker in _BLOCKER_MARKERS):
                raise RenderedOfficialError("页面要求登录或人工验证；程序不会绕过")
            raw_links = raw.get("links")
            links = tuple(item for item in raw_links if isinstance(item, Mapping)) if isinstance(raw_links, list) else ()
            inferred_company = _clean(company or raw.get("company"), 200)
            jobs = jobs_from_rendered_links(
                final_url,
                links,
                company=inferred_company,
                max_jobs=max_jobs,
            )
            warnings = () if jobs else ("动态页面已打开，但未发现可安全识别的同域岗位链接",)
            return RenderedDiscoveryResult(jobs, final_url, warnings)
        except RenderedOfficialError:
            raise
        except Exception as exc:
            raise RenderedOfficialError("动态招聘页面读取失败；没有绕过网站限制") from exc
        finally:
            context.close()
            browser.close()


__all__ = [
    "RenderedDiscoveryResult",
    "RenderedOfficialError",
    "discover_rendered_jobs",
    "jobs_from_rendered_links",
]
