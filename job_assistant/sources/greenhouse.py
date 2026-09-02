from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from ..models import Job
from ..salary_parser import parse_salary

ALLOWED_BOARD_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io"}
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class SourceError(RuntimeError):
    pass


def extract_board_token(value: str) -> str:
    candidate = value.strip()
    if "://" in candidate:
        parsed = urlparse(candidate)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_BOARD_HOSTS:
            raise SourceError("仅支持 Greenhouse 官方 HTTPS 职位页")
        parts = [part for part in parsed.path.split("/") if part]
        if not parts:
            raise SourceError("职位页地址中缺少 board token")
        candidate = parts[0]
    if not TOKEN_PATTERN.fullmatch(candidate):
        raise SourceError("board token 格式无效")
    return candidate


def _plain_text(content: str) -> str:
    # Greenhouse 的 content 可能包含转义后的 HTML。
    decoded = html.unescape(html.unescape(content or ""))
    return BeautifulSoup(decoded, "html.parser").get_text(" ", strip=True)


def _parse_jobs(
    payload: dict[str, Any], token: str, company_name: str | None = None
) -> list[Job]:
    if not isinstance(payload, dict):
        raise SourceError("Greenhouse 返回的数据结构无效")
    raw_jobs = payload.get("jobs", [])
    if not isinstance(raw_jobs, list):
        raise SourceError("Greenhouse 返回的岗位列表无效")
    jobs: list[Job] = []
    for raw in raw_jobs:
        if not isinstance(raw, dict):
            continue
        departments = raw.get("departments") or []
        if not isinstance(departments, list):
            departments = []
        department = ", ".join(
            str(item.get("name", "")).strip()
            for item in departments
            if isinstance(item, dict)
            if str(item.get("name", "")).strip()
        )
        absolute_url = str(raw.get("absolute_url", "")).strip()
        parsed_url = urlparse(absolute_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            continue
        description = _plain_text(str(raw.get("content", "")))
        parsed_salary = parse_salary(description)
        location_value = raw.get("location") or {}
        if not isinstance(location_value, dict):
            location_value = {}
        jobs.append(
            Job(
                id=str(raw.get("id", "")),
                title=str(raw.get("title", "")).strip(),
                company=company_name or token,
                location=str(location_value.get("name", "")).strip(),
                description=description,
                url=absolute_url,
                source=f"Greenhouse:{token}",
                department=department,
                updated_at=str(raw.get("updated_at", "")),
                salary_min=parsed_salary.minimum if parsed_salary else None,
                salary_max=parsed_salary.maximum if parsed_salary else None,
                currency=parsed_salary.currency if parsed_salary else "",
                period=parsed_salary.period if parsed_salary else "",
                salary_text=parsed_salary.text if parsed_salary else "",
                language=str(raw.get("language", "")).strip(),
            )
        )
    return [job for job in jobs if job.id and job.title and job.url]


class GreenhouseSource:
    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch(self, board_url_or_token: str) -> list[Job]:
        token = extract_board_token(board_url_or_token)
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
        try:
            response = requests.get(
                url,
                params={"content": "true"},
                headers={"User-Agent": "SafeJobAssistant/0.1 (human-in-the-loop)"},
                timeout=self.timeout_seconds,
                allow_redirects=False,
            )
            if 300 <= response.status_code < 400:
                raise SourceError("Greenhouse API 返回了意外重定向，已停止读取")
            response.raise_for_status()
            content_length = int(response.headers.get("Content-Length", "0") or 0)
            if content_length > MAX_RESPONSE_BYTES or len(response.content) > MAX_RESPONSE_BYTES:
                raise SourceError("Greenhouse 返回数据过大，已停止读取")
            payload = response.json()
        except SourceError:
            raise
        except requests.RequestException as exc:
            raise SourceError(f"读取 Greenhouse 职位失败：{exc}") from exc
        except ValueError as exc:
            raise SourceError("Greenhouse 返回了无法解析的数据") from exc

        company_name = self._fetch_company_name(token)
        jobs = _parse_jobs(payload, token, company_name)
        if not jobs:
            raise SourceError("这个职位页当前没有可读取的公开岗位")
        return jobs[:500]

    def _fetch_company_name(self, token: str) -> str:
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}"
        try:
            response = requests.get(
                url,
                headers={"User-Agent": "SafeJobAssistant/0.2 (human-in-the-loop)"},
                timeout=self.timeout_seconds,
                allow_redirects=False,
            )
            if response.status_code != 200 or len(response.content) > 1024 * 1024:
                return token
            value = str(response.json().get("name", "")).strip()
            return value or token
        except (requests.RequestException, ValueError, AttributeError):
            return token
