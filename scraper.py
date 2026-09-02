"""BOSS直聘/猎聘岗位的受控导入边界。

这两个平台当前不允许第三方爬虫、拟人程序或规避技术措施抓取职位，
因此本模块不会联网、不会接管登录 Cookie，也不会隐藏自动化标记。
它只负责生成官方搜索入口，以及解析用户主动复制或保存到本机的岗位资料。

如果未来取得平台书面授权，应在独立的授权适配器中实现官方 API/浏览器接入，
并保留限速、验证码人工接管和最终提交确认。本模块的 ``live_scrape`` 会一直
保持显式拒绝，避免一次 UI 改动意外恢复未授权抓取。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from job_assistant.models import Job
from job_assistant.salary_parser import parse_salary


MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_JOB_COUNT = 50
MAX_FIELD_CHARS = 500
MAX_DESCRIPTION_CHARS = 50_000
MAX_URL_CHARS = 4_096


class JobImportError(ValueError):
    """用户提供的岗位资料不完整或不安全。"""


class RestrictedPlatformError(RuntimeError):
    """调用方试图启动未获平台授权的自动抓取。"""


@dataclass(frozen=True, slots=True)
class PlatformSpec:
    key: str
    display_name: str
    home_url: str
    search_path: str
    search_keyword_name: str
    allowed_hosts: tuple[str, ...]
    required_job_path: str
    source: str


PLATFORMS: dict[str, PlatformSpec] = {
    "boss": PlatformSpec(
        key="boss",
        display_name="BOSS直聘",
        home_url="https://www.zhipin.com/",
        search_path="https://www.zhipin.com/web/geek/job",
        search_keyword_name="query",
        allowed_hosts=("www.zhipin.com",),
        required_job_path="/job_detail/",
        source="UserProvided:BOSS直聘",
    ),
    "liepin": PlatformSpec(
        key="liepin",
        display_name="猎聘",
        home_url="https://www.liepin.com/",
        search_path="https://www.liepin.com/zhaopin/",
        search_keyword_name="key",
        allowed_hosts=("www.liepin.com",),
        required_job_path="/job/",
        source="UserProvided:猎聘",
    ),
}

PLATFORM_ALIASES = {
    "boss": "boss",
    "boss直聘": "boss",
    "直聘": "boss",
    "zhipin": "boss",
    "猎聘": "liepin",
    "liepin": "liepin",
}

# BOSS 的公开城市代码。未知城市不会被猜测，UI 可退回平台官网自行选择。
BOSS_CITY_CODES = {
    "上海": "101020100",
    "北京": "101010100",
    "深圳": "101280600",
    "广州": "101280100",
    "杭州": "101210100",
    "苏州": "101190400",
    "南京": "101190100",
    "成都": "101270100",
    "武汉": "101200100",
    "西安": "101110100",
}

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_JOB_SEPARATOR_RE = re.compile(r"(?im)^\s*={3,}\s*(?:job|岗位)?\s*={3,}\s*$")
_FIELD_LINE_RE = re.compile(
    r"^\s*(岗位名称|职位名称|title|job[_ ]?title|公司名称|公司|company|"
    r"工作地点|地点|location|薪资范围|薪资|salary|岗位链接|职位链接|链接|url|"
    r"职位描述|岗位描述|jd|description)\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)

_FIELD_NAMES = {
    "岗位名称": "title",
    "职位名称": "title",
    "title": "title",
    "job title": "title",
    "job_title": "title",
    "公司名称": "company",
    "公司": "company",
    "company": "company",
    "工作地点": "location",
    "地点": "location",
    "location": "location",
    "薪资范围": "salary_text",
    "薪资": "salary_text",
    "salary": "salary_text",
    "岗位链接": "url",
    "职位链接": "url",
    "链接": "url",
    "url": "url",
    "职位描述": "description",
    "岗位描述": "description",
    "jd": "description",
    "description": "description",
}
_JOB_DETAIL_PATH_PATTERNS = {
    "boss": re.compile(r"^/job_detail/[A-Za-z0-9._~-]+\.html$", re.IGNORECASE),
    "liepin": re.compile(r"^/job/[A-Za-z0-9._~-]+\.s?html$", re.IGNORECASE),
}

IMPORT_TEMPLATE = """岗位名称：热管理工程师（校招）
公司名称：示例公司
工作地点：上海
薪资范围：15-25K/月
岗位链接：https://www.zhipin.com/job_detail/请替换为真实岗位ID.html
职位描述：
请粘贴官网中你本人正在查看的完整 JD。

===JOB===

岗位名称：第二个岗位（可删除本段）
公司名称：示例公司
工作地点：上海
薪资范围：未公开
岗位链接：https://www.zhipin.com/job_detail/请替换为真实岗位ID.html
职位描述：
多个岗位之间使用 ===JOB=== 分隔。
"""


def job_import_template(platform: str) -> str:
    """返回所选平台可直接填写的纯文本模板。"""

    spec = _platform(platform)
    if spec.key == "boss":
        return IMPORT_TEMPLATE
    return IMPORT_TEMPLATE.replace(
        "https://www.zhipin.com/job_detail/请替换为真实岗位ID.html",
        "https://www.liepin.com/job/请替换为真实岗位ID.shtml",
    )


def _platform(value: str) -> PlatformSpec:
    key = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    try:
        return PLATFORMS[PLATFORM_ALIASES[key]]
    except KeyError as exc:
        raise JobImportError("岗位平台必须是 BOSS直聘 或 猎聘") from exc


def build_official_search_url(
    platform: str,
    keyword: str,
    *,
    city: str = "上海",
) -> str:
    """生成供用户亲自在普通浏览器中打开的官方搜索链接。"""

    spec = _platform(platform)
    clean_keyword = _clean_scalar(keyword, "搜索关键词", required=True, limit=100)
    query: dict[str, str] = {spec.search_keyword_name: clean_keyword}
    if spec.key == "boss":
        code = BOSS_CITY_CODES.get(_clean_scalar(city, "城市", required=True, limit=30))
        if not code:
            raise JobImportError("暂不认识该 BOSS 城市代码，请从 App 中提供的城市选择")
        query["city"] = code
    return f"{spec.search_path}?{urlencode(query)}"


def official_home_url(platform: str) -> str:
    return _platform(platform).home_url


def live_scrape(platform: str, *_: object, **__: object) -> list[Job]:
    """显式阻止未授权实时抓取；在启动 Playwright 之前就失败。"""

    spec = _platform(platform)
    raise RestrictedPlatformError(
        f"{spec.display_name} 当前未配置书面授权或官方 API；"
        "本应用不会启动自动抓取、隐藏 webdriver 或模拟真人规避风控。"
    )


def parse_pasted_jobs(text: str, platform: str) -> list[Job]:
    """解析一段或多段用户主动复制的结构化岗位资料。"""

    spec = _platform(platform)
    normalized = _clean_multiline(text, "岗位资料", MAX_IMPORT_BYTES)
    if not normalized:
        raise JobImportError("请粘贴至少一个岗位的真实信息")
    records: list[dict[str, str]] = []
    for block_number, block in enumerate(_JOB_SEPARATOR_RE.split(normalized), start=1):
        if not block.strip():
            continue
        record: dict[str, str] = {}
        active_multiline_field = ""
        description_lines: list[str] = []
        for line in block.splitlines():
            match = _FIELD_LINE_RE.match(line)
            if match:
                label = unicodedata.normalize("NFKC", match.group(1)).strip().casefold()
                field = _FIELD_NAMES[label]
                value = match.group(2).strip()
                active_multiline_field = field if field == "description" else ""
                if field == "description":
                    if value:
                        description_lines.append(value)
                else:
                    record[field] = value
                continue
            if active_multiline_field == "description":
                description_lines.append(line)
        if description_lines:
            record["description"] = "\n".join(description_lines).strip()
        try:
            records.append(_validated_record(record, spec))
        except JobImportError as exc:
            raise JobImportError(f"第 {block_number} 个岗位：{exc}") from exc
    return _records_to_jobs(records, spec)


def parse_json_jobs(data: str | bytes, platform: str) -> list[Job]:
    """解析用户上传的 JSON，支持数组或 ``{"jobs": [...]}``。"""

    spec = _platform(platform)
    text = _decode_upload(data, "JSON")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JobImportError(f"JSON 格式错误（第 {exc.lineno} 行）") from exc
    records = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise JobImportError('JSON 顶层必须是数组，或包含 "jobs" 数组')
    validated: list[dict[str, Any]] = []
    for index, item in enumerate(records, start=1):
        if not isinstance(item, Mapping):
            raise JobImportError(f"第 {index} 个岗位必须是对象")
        try:
            validated.append(_validated_record(_flatten_mapping(item), spec))
        except JobImportError as exc:
            raise JobImportError(f"第 {index} 个岗位：{exc}") from exc
    return _records_to_jobs(validated, spec)


def parse_saved_html(data: str | bytes, platform: str) -> list[Job]:
    """从用户本人保存的岗位详情 HTML 中读取 schema.org JobPosting。"""

    spec = _platform(platform)
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    _validate_upload_size(raw)
    soup = BeautifulSoup(raw, "html.parser")
    records: list[dict[str, Any]] = []
    for script in soup.find_all("script"):
        script_type = str(script.get("type") or "").casefold()
        if "ld+json" not in script_type:
            continue
        content = script.string or script.get_text("", strip=True)
        if not content:
            continue
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        for item in _walk_jsonld(payload):
            type_value = item.get("@type")
            types = type_value if isinstance(type_value, list) else [type_value]
            if not any(str(value).casefold() == "jobposting" for value in types):
                continue
            flattened = _flatten_mapping(item)
            if not flattened.get("url"):
                flattened["url"] = _canonical_url(soup)
            try:
                records.append(_validated_record(flattened, spec))
            except JobImportError:
                continue
    if not records:
        raise JobImportError(
            "保存的 HTML 中没有完整 JobPosting 数据；请改用上方模板粘贴岗位信息"
        )
    return _records_to_jobs(records, spec)


def parse_imported_file(filename: str, data: bytes, platform: str) -> list[Job]:
    """按扩展名解析用户上传的 JSON、HTML 或 TXT 文件。"""

    _validate_upload_size(data)
    suffix = str(filename or "").rsplit(".", 1)[-1].casefold()
    if suffix == "json":
        return parse_json_jobs(data, platform)
    if suffix in {"html", "htm"}:
        return parse_saved_html(data, platform)
    if suffix in {"txt", "md"}:
        return parse_pasted_jobs(_decode_upload(data, "文本"), platform)
    raise JobImportError("岗位文件仅支持 JSON、HTML、HTM、TXT 或 MD")


def merge_jobs(*groups: list[Job]) -> list[Job]:
    """按规范化链接去重，同时保持用户提供的先后顺序。"""

    merged: list[Job] = []
    seen: set[str] = set()
    for jobs in groups:
        for job in jobs:
            if job.url in seen:
                continue
            seen.add(job.url)
            merged.append(job)
            if len(merged) > MAX_JOB_COUNT:
                raise JobImportError(f"一次最多导入 {MAX_JOB_COUNT} 个岗位")
    if not merged:
        raise JobImportError("没有可导入的岗位")
    return merged


def _clean_scalar(value: object, label: str, *, required: bool, limit: int) -> str:
    if isinstance(value, (dict, list, tuple, set)):
        raise JobImportError(f"{label}格式不正确")
    text = unicodedata.normalize("NFKC", str(value or ""))
    if _CONTROL_RE.search(text) or "\r" in text or "\n" in text:
        raise JobImportError(f"{label}包含无效控制字符")
    text = re.sub(r"[ \t]+", " ", text).strip()
    if required and not text:
        raise JobImportError(f"{label}不能为空")
    if len(text) > limit:
        raise JobImportError(f"{label}过长")
    return text


def _clean_multiline(value: object, label: str, limit: int) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).replace("\r\n", "\n")
    if _CONTROL_RE.search(text):
        raise JobImportError(f"{label}包含无效控制字符")
    if len(text.encode("utf-8")) > limit:
        raise JobImportError(f"{label}超过 {limit // 1024} KB")
    lines = [line.rstrip() for line in text.replace("\r", "\n").splitlines()]
    return "\n".join(lines).strip()


def _validate_job_url(value: object, spec: PlatformSpec) -> str:
    candidate = _clean_scalar(value, "岗位链接", required=True, limit=MAX_URL_CHARS)
    if "\\" in candidate:
        raise JobImportError("岗位链接格式无效")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise JobImportError("岗位链接格式无效") from exc
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme.casefold() != "https":
        raise JobImportError("岗位链接必须使用 HTTPS")
    if parsed.username is not None or parsed.password is not None or port not in (None, 443):
        raise JobImportError("岗位链接不能包含账号、口令或非标准端口")
    if hostname not in spec.allowed_hosts:
        raise JobImportError(f"岗位链接必须来自 {spec.display_name} 官方域名")
    path = parsed.path or "/"
    if spec.required_job_path not in path.casefold():
        raise JobImportError("请粘贴单个岗位详情页链接，不要粘贴搜索页")
    if not _JOB_DETAIL_PATH_PATTERNS[spec.key].fullmatch(path):
        raise JobImportError("岗位详情页路径格式无效，请替换模板中的示例链接")
    # 丢弃追踪参数和 fragment，避免持久化无关标识或临时令牌。
    return urlunsplit(("https", hostname, path, "", ""))


def _validated_record(record: Mapping[str, Any], spec: PlatformSpec) -> dict[str, Any]:
    title = _clean_scalar(record.get("title"), "岗位名称", required=True, limit=MAX_FIELD_CHARS)
    company = _clean_scalar(record.get("company"), "公司名称", required=True, limit=MAX_FIELD_CHARS)
    location = _clean_scalar(record.get("location"), "工作地点", required=True, limit=MAX_FIELD_CHARS)
    description = _clean_multiline(record.get("description"), "职位描述", MAX_DESCRIPTION_CHARS)
    if not description:
        raise JobImportError("职位描述不能为空")
    url = _validate_job_url(record.get("url"), spec)
    salary_text = _clean_scalar(
        record.get("salary_text"), "薪资范围", required=False, limit=MAX_FIELD_CHARS
    )
    return {
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "url": url,
        "salary_text": salary_text,
        "department": _clean_scalar(
            record.get("department"), "部门", required=False, limit=MAX_FIELD_CHARS
        ),
        "updated_at": _clean_scalar(
            record.get("updated_at"), "更新时间", required=False, limit=MAX_FIELD_CHARS
        ),
        "salary_min": _optional_number(record.get("salary_min"), "最低薪资"),
        "salary_max": _optional_number(record.get("salary_max"), "最高薪资"),
        "currency": _clean_scalar(
            record.get("currency"), "薪资币种", required=False, limit=20
        ),
        "period": _clean_scalar(
            record.get("period"), "计薪周期", required=False, limit=20
        ),
    }


def _records_to_jobs(records: list[dict[str, Any]], spec: PlatformSpec) -> list[Job]:
    if len(records) > MAX_JOB_COUNT:
        raise JobImportError(f"一次最多导入 {MAX_JOB_COUNT} 个岗位")
    jobs: list[Job] = []
    for record in records:
        salary_context = f"薪资范围：{record['salary_text']}。{record['description']}"
        parsed_salary = parse_salary(salary_context)
        salary_min = record.get("salary_min")
        salary_max = record.get("salary_max")
        if salary_min is None and parsed_salary:
            salary_min = parsed_salary.minimum
        if salary_max is None and parsed_salary:
            salary_max = parsed_salary.maximum
        if salary_min is not None and salary_max is not None and salary_min > salary_max:
            raise JobImportError("最低薪资不能高于最高薪资")
        url = record["url"]
        job_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        jobs.append(
            Job(
                id=f"{spec.key}-{job_id}",
                title=record["title"],
                company=record["company"],
                location=record["location"],
                description=record["description"],
                url=url,
                source=spec.source,
                department=record.get("department", ""),
                updated_at=record.get("updated_at", ""),
                salary_min=salary_min,
                salary_max=salary_max,
                currency=record.get("currency") or (parsed_salary.currency if parsed_salary else ""),
                period=record.get("period") or (parsed_salary.period if parsed_salary else ""),
                salary_text=record.get("salary_text")
                or (parsed_salary.text if parsed_salary else ""),
                language=_language(record["title"] + " " + record["description"]),
            )
        )
    return merge_jobs(jobs)


def _optional_number(value: object, label: str) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise JobImportError(f"{label}必须是数字")
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise JobImportError(f"{label}必须是数字") from exc
    if not math.isfinite(number) or number < 0:
        raise JobImportError(f"{label}必须是有限非负数")
    return number


def _flatten_mapping(record: Mapping[str, Any]) -> dict[str, Any]:
    def first(*keys: str) -> Any:
        for key in keys:
            if key in record and record[key] not in (None, ""):
                return record[key]
        return ""

    company = first("company", "company_name", "公司名称")
    organization = record.get("hiringOrganization")
    if not company and isinstance(organization, Mapping):
        company = organization.get("name", "")
    location = first("location", "job_location", "工作地点")
    if not location:
        location = _jsonld_location(record.get("jobLocation"))
    description = first("description", "job_description", "职位描述", "jd")
    if description:
        description = _html_to_text(description)
    salary = first("salary_text", "salary", "薪资范围")
    salary_details = _jsonld_salary(record.get("baseSalary"))
    if not salary:
        salary = salary_details.get("salary_text", "")
    return {
        "title": first("title", "job_title", "岗位名称", "职位名称"),
        "company": company,
        "location": location,
        "description": description,
        "url": first("url", "job_url", "岗位链接", "职位链接"),
        "salary_text": salary,
        "department": first("department", "部门"),
        "updated_at": first("updated_at", "datePosted", "更新时间"),
        "salary_min": first("salary_min", "minimum") or salary_details.get("salary_min"),
        "salary_max": first("salary_max", "maximum") or salary_details.get("salary_max"),
        "currency": first("currency", "salaryCurrency") or salary_details.get("currency", ""),
        "period": first("period", "salary_period") or salary_details.get("period", ""),
    }


def _html_to_text(value: object) -> str:
    soup = BeautifulSoup(str(value or ""), "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    return soup.get_text("\n", strip=True)


def _jsonld_location(value: object) -> str:
    items = value if isinstance(value, list) else [value]
    locations: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        address = item.get("address")
        if isinstance(address, Mapping):
            parts = [
                str(address.get(key) or "").strip()
                for key in ("addressLocality", "addressRegion", "addressCountry")
            ]
            location = " ".join(part for part in parts if part)
            if location and location not in locations:
                locations.append(location)
    return " / ".join(locations)


def _jsonld_salary(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    currency = str(value.get("currency") or "").strip()
    amount = value.get("value")
    if not isinstance(amount, Mapping):
        return {}
    minimum = amount.get("minValue")
    maximum = amount.get("maxValue")
    unit = str(amount.get("unitText") or "").strip().casefold()
    periods = {"month": "month", "year": "year", "hour": "hour", "day": "day"}
    period = periods.get(unit, unit)
    if minimum in (None, "") or maximum in (None, ""):
        return {}
    return {
        "salary_min": minimum,
        "salary_max": maximum,
        "currency": currency,
        "period": period,
        "salary_text": f"{currency} {minimum}-{maximum}/{period}".strip(" /"),
    }


def _walk_jsonld(value: object):
    if isinstance(value, list):
        for item in value:
            yield from _walk_jsonld(item)
    elif isinstance(value, Mapping):
        yield value
        graph = value.get("@graph")
        if graph is not None:
            yield from _walk_jsonld(graph)


def _canonical_url(soup: BeautifulSoup) -> str:
    canonical = soup.find("link", rel=lambda value: value and "canonical" in value)
    if canonical and canonical.get("href"):
        return str(canonical["href"])
    social = soup.find("meta", attrs={"property": "og:url"})
    return str(social.get("content") or "") if social else ""


def _language(text: str) -> str:
    chinese = sum("\u4e00" <= char <= "\u9fff" for char in text)
    latin = sum(char.isascii() and char.isalpha() for char in text)
    return "zh" if chinese >= max(10, latin // 3) else "en"


def _validate_upload_size(data: bytes) -> None:
    if not data:
        raise JobImportError("岗位文件为空")
    if len(data) > MAX_IMPORT_BYTES:
        raise JobImportError(f"岗位文件不能超过 {MAX_IMPORT_BYTES // 1024 // 1024} MB")


def _decode_upload(data: str | bytes, label: str) -> str:
    if isinstance(data, str):
        encoded = data.encode("utf-8")
        _validate_upload_size(encoded)
        return data
    raw = bytes(data)
    _validate_upload_size(raw)
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise JobImportError(f"{label}文件必须使用 UTF-8 或 GB18030 编码")


__all__ = [
    "BOSS_CITY_CODES",
    "IMPORT_TEMPLATE",
    "JobImportError",
    "MAX_JOB_COUNT",
    "PLATFORMS",
    "RestrictedPlatformError",
    "build_official_search_url",
    "job_import_template",
    "live_scrape",
    "merge_jobs",
    "official_home_url",
    "parse_imported_file",
    "parse_json_jobs",
    "parse_pasted_jobs",
    "parse_saved_html",
]
