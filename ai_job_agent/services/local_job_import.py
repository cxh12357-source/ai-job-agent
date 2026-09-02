"""Read a deliberately small, data-only bridge from local Playwright to Cloud.

No URL is fetched and no browser state, candidate profile or Python code is
accepted.  Imported text remains untrusted job-page content, not instructions.
URLs with authentication query parameters are rejected rather than silently
retaining credentials.  Host checks are syntactic; DNS is never resolved here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ai_job_agent.models import JobPosting


MAX_EXPORT_BYTES = 5 * 1024 * 1024
MAX_EXPORT_JOBS = 100
EXPORT_KIND = "charles-job-export"
EXPORT_SOURCE = "local-playwright"

_FIELD_LIMITS = {
    "title": 300,
    "company": 300,
    "location": 500,
    "description": 60_000,
    "requirements": 20_000,
    "job_url": 4096,
    "job_type": 200,
    "department": 300,
    "publish_date": 100,
}
_JOB_FIELDS = set(_FIELD_LIMITS) | {"source"}
_ENVELOPE_FIELDS = {"schema_version", "kind", "exported_at", "source", "jobs", "warnings"}
_PRIVATE_METADATA = {
    "candidate", "candidateprofile", "profile", "resume", "cookies", "cookie",
    "storagestate", "localstorage", "sessionstorage", "password", "headers",
    "authorization", "openaiapikey", "apikey", "accesstoken", "refreshtoken",
}
_SENSITIVE_QUERY_KEYS = {
    "auth", "authorization", "apikey", "key", "token", "jwt", "bearer",
    "password", "passwd", "secret", "session", "sessionid", "sid", "sso",
    "cookie", "code", "state", "signature", "sig", "email", "phone",
    "mobile", "username", "userid", "user",
    "accesskey", "securityid", "ticket", "ticketid", "samlresponse", "samlrequest",
}


class LocalJobImportError(ValueError):
    """A local export cannot safely be accepted by the website."""


@dataclass(frozen=True)
class LocalJobImportResult:
    jobs: list[JobPosting]
    warnings: list[str]
    exported_at: str | None = None


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise LocalJobImportError("JSON 中包含重复字段，请重新导出。")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    raise LocalJobImportError("JSON 中不能包含 NaN 或 Infinity。")


def _plain_text(value: object, field: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是文本")
    if len(value) > _FIELD_LIMITS[field]:
        raise ValueError(f"{field} 超过长度限制")
    # Parse even short HTML labels, and never render exported HTML in the app.
    soup = BeautifulSoup(value, "html.parser")
    for node in soup(["script", "style", "noscript", "template"]):
        node.decompose()
    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    text = text.replace("\x00", "").strip()
    if required and not text:
        raise ValueError(f"{field} 不能为空")
    return text or None


def _canonical_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("job_url 不能为空")
    value = value.strip()
    if len(value) > _FIELD_LIMITS["job_url"]:
        raise ValueError("job_url 超过长度限制")
    if re.search(r"[\s\x00-\x1f\x7f\\]", value):
        raise ValueError("job_url 包含非法字符")
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except ValueError as exc:
        raise ValueError("job_url 格式无效") from exc
    if parsed.scheme.lower() not in {"https", "http"} or not hostname:
        raise ValueError("job_url 必须为公共 HTTP/HTTPS 地址")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("job_url 不能包含账号或密码")
    if "%" in hostname or hostname == "localhost" or hostname.endswith(
        (".localhost", ".local", ".internal", ".lan", ".home")
    ):
        raise ValueError("job_url 不能指向本地或内部地址")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        # Numeric/hex browser IPv4 shortcuts must not become localhost links.
        try:
            hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("job_url 主机名无效") from exc
        labels = hostname.split(".")
        if (
            len(labels) < 2
            or not re.fullmatch(r"[a-z][a-z0-9-]*", labels[-1])
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
            or all(re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)", label) for label in labels)
        ):
            raise ValueError("job_url 必须为公共主机名")
    else:
        if not address.is_global or address.is_reserved or address.is_multicast:
            raise ValueError("job_url 不能指向本地、私有或保留 IP")
        hostname = f"[{address.compressed}]" if address.version == 6 else str(address)
    for parameter, _ in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = _key(parameter)
        if (
            normalized in _SENSITIVE_QUERY_KEYS
            or any(part in normalized for part in ("token", "password", "secret", "credential", "signature", "cookie"))
            or normalized.endswith(("apikey", "sessionid", "authorization"))
        ):
            raise ValueError("job_url 含登录令牌或个人信息参数，请导出公开岗位链接")
    default_port = (parsed.scheme.lower() == "https" and port == 443) or (
        parsed.scheme.lower() == "http" and port == 80
    )
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    # Keep meaningful query ordering/content; only strip fragments and defaults.
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))


def _parse_job(record: object) -> tuple[JobPosting, bool]:
    if not isinstance(record, dict):
        raise ValueError("岗位必须为 JSON 对象")
    data: dict = {}
    for field in ("title", "company", "description"):
        data[field] = _plain_text(record.get(field), field, required=True)
    for field in ("location", "job_type", "department", "publish_date"):
        data[field] = _plain_text(record.get(field), field)
    requirements = record.get("requirements")
    if isinstance(requirements, list):
        if len(requirements) > 100 or any(not isinstance(item, str) for item in requirements):
            raise ValueError("requirements 列表格式或数量无效")
        if sum(len(item) for item in requirements) > _FIELD_LIMITS["requirements"]:
            raise ValueError("requirements 超过长度限制")
        data["requirements"] = [text for item in requirements if (text := _plain_text(item, "requirements"))] or None
    else:
        data["requirements"] = _plain_text(requirements, "requirements")
    data["job_url"] = _canonical_url(record.get("job_url"))
    identifier = "local:" + hashlib.sha256(data["job_url"].encode("utf-8")).hexdigest()[:24]
    data.update(id=identifier, job_id=identifier, source=EXPORT_SOURCE)
    return JobPosting(**data), bool(set(record) - _JOB_FIELDS)


def parse_local_job_export(filename: str, content: bytes) -> LocalJobImportResult:
    """Validate a v1 export, skipping invalid records without saving raw input.

    File-level format/size/privacy errors reject the whole file. Per-job errors
    produce a bounded warning with its 1-based index, never its raw values. All
    unknown record fields (including IDs, cookies and candidate facts) are
    discarded. The website should not persist the original upload.
    """
    if not isinstance(filename, str) or Path(filename).suffix.casefold() != ".json":
        raise LocalJobImportError("只支持本地抓取器导出的 .json 文件。")
    if not isinstance(content, bytes) or not content:
        raise LocalJobImportError("导入文件为空或不是有效二进制内容。")
    if len(content) > MAX_EXPORT_BYTES:
        raise LocalJobImportError("导入文件不能超过 5 MB。")
    try:
        payload = json.loads(
            content.decode("utf-8-sig"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise LocalJobImportError("文件不是有效的 UTF-8 JSON，请重新导出。") from exc
    if not isinstance(payload, dict):
        raise LocalJobImportError("导出文件必须包含版本信息和 jobs 列表。")
    if any(_key(key) in _PRIVATE_METADATA for key in payload):
        raise LocalJobImportError("检测到候选人资料或登录状态字段。请只上传岗位导出文件，不要上传 Cookie、简历档案或浏览器状态。")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise LocalJobImportError("不支持此导出版本；需要 schema_version=1。")
    if payload.get("kind") != EXPORT_KIND or payload.get("source") != EXPORT_SOURCE:
        raise LocalJobImportError("不是 Charles 本地抓取器岗位导出文件。")
    scraper_warnings = payload.get("warnings", [])
    if (
        not isinstance(scraper_warnings, list)
        or len(scraper_warnings) > 30
        or any(not isinstance(item, str) or len(item) > 400 for item in scraper_warnings)
    ):
        raise LocalJobImportError("warnings 必须为最多 30 条、每条最多 400 字符的文本列表。")
    exported_at = payload.get("exported_at")
    if exported_at is not None:
        if not isinstance(exported_at, str) or len(exported_at) > 80:
            raise LocalJobImportError("exported_at 必须为 ISO 日期时间或 null。")
        try:
            datetime.fromisoformat(exported_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise LocalJobImportError("exported_at 必须为 ISO 日期时间或 null。") from exc
    records = payload.get("jobs")
    if not isinstance(records, list) or not records:
        raise LocalJobImportError("导出文件没有岗位，请先在本机抓取后重新导出。")
    if len(records) > MAX_EXPORT_JOBS:
        raise LocalJobImportError("单次最多导入 100 个岗位，请缩小抓取范围。")

    warnings = []
    if scraper_warnings:
        # Logs may contain page URLs or secrets. Never display or persist them.
        warnings.append(f"本机抓取器报告 {len(scraper_warnings)} 条提示，请在本机查看原因。")
    if set(payload) - _ENVELOPE_FIELDS:
        warnings.append("已忽略导出文件中的额外元数据。")
    jobs = []
    seen = set()
    for index, record in enumerate(records, start=1):
        try:
            job, ignored_fields = _parse_job(record)
        except (ValueError, TypeError) as exc:
            warnings.append(f"第 {index} 条岗位已跳过：{exc}。")
            continue
        if job.job_url in seen:
            warnings.append(f"第 {index} 条岗位与前面的公开链接重复，已跳过。")
            continue
        seen.add(job.job_url)
        jobs.append(job)
        if ignored_fields:
            warnings.append(f"第 {index} 条岗位的额外字段已丢弃，只保留公开岗位信息。")
    if not jobs:
        details = "；".join(warnings[:3])
        raise LocalJobImportError(f"没有可导入的有效岗位。{details}")
    return LocalJobImportResult(jobs=jobs, warnings=warnings, exported_at=exported_at)


def local_job_export_template() -> str:
    """Return a non-personal example of the interchange format, not a vacancy."""
    return json.dumps(
        {
            "schema_version": 1,
            "kind": EXPORT_KIND,
            "source": EXPORT_SOURCE,
            "exported_at": None,
            "warnings": [],
            "jobs": [
                {
                    "title": "示例岗位（仅用于演示，请替换为真实抓取数据）",
                    "company": "示例公司",
                    "location": None,
                    "job_type": "实习",
                    "department": None,
                    "description": "这是一条格式示例，并非真实招聘信息。请用公司公开岗位页中的真实 JD 替换。",
                    "requirements": None,
                    "job_url": "https://example.com/careers/sample-job",
                    "source": EXPORT_SOURCE,
                    "publish_date": None,
                }
            ],
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"
