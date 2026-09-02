#!/usr/bin/env python3
"""Boss直聘/猎聘本地简历打招呼与匹配助手。

设计边界：
1. 自动化只负责搜索、读取职位信息、生成草稿。
2. 默认完全不点击沟通/投递按钮；必须传入 --enable-actions 才开放逐岗位确认。
3. 即使开放操作，也只在用户输入精确确认口令后点击一次入口按钮；最终粘贴与发送
   招呼语仍由用户在浏览器中手动完成。
4. 遇到验证码、风控、登录失效、按钮歧义或网页结构变化时立即停止，不尝试绕过。

网站页面会不定期改版。所有站点选择器集中在 SITE_CONFIGS，便于按实际页面维护。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import random
import re
import sqlite3
import sys
import time
import unicodedata
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import parse_qsl, quote, urljoin, urlsplit, urlunsplit


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_RESUME_PATH = PROJECT_DIR / "resume.json"
DEFAULT_DB_PATH = PROJECT_DIR / "data" / "interactions.db"
DEFAULT_PROFILE_ROOT = PROJECT_DIR / "data" / "browser_profiles"
DEFAULT_OUTPUT_PATH = PROJECT_DIR / "data" / "application_list.json"
MAX_JD_CHARS = 8_000
MAX_RESULTS = 20
DEFAULT_MIN_SCORE = 45

# 这些状态说明一次外部沟通可能已经发生，因此默认禁止再次尝试，防止重复投递。
BLOCKING_STATUSES = frozenset({"confirmed", "opened", "sent", "uncertain"})
ALL_STATUSES = frozenset(
    {"draft", "cancelled", "confirmed", "opened", "sent", "uncertain", "failed"}
)


@dataclass(frozen=True)
class SiteConfig:
    """一个招聘站点的页面信息和有序选择器回退表。"""

    key: str
    display_name: str
    home_url: str
    search_url_template: str
    allowed_domains: tuple[str, ...]
    search_inputs: tuple[str, ...]
    card_selectors: tuple[str, ...]
    title_selectors: tuple[str, ...]
    company_selectors: tuple[str, ...]
    salary_selectors: tuple[str, ...]
    link_selectors: tuple[str, ...]
    jd_selectors: tuple[str, ...]
    hr_selectors: tuple[str, ...]
    action_texts: tuple[str, ...]
    already_action_texts: tuple[str, ...]


# 选择器按“语义稳定性较高 -> CSS 类名回退”的顺序排列。
# 若站点改版，只需要根据浏览器开发者工具更新此处，不应改动安全确认流程。
SITE_CONFIGS: dict[str, SiteConfig] = {
    "boss": SiteConfig(
        key="boss",
        display_name="Boss直聘",
        home_url="https://www.zhipin.com/",
        search_url_template="https://www.zhipin.com/web/geek/job?query={keyword}",
        allowed_domains=("zhipin.com",),
        search_inputs=(
            "input[placeholder*='搜索职位']",
            "input[placeholder*='职位或公司']",
            "input[name='query']",
            ".search-input-box input",
            ".search-form input.ipt-search",
        ),
        card_selectors=(
            ".job-card-wrapper",
            ".job-list-box li",
            ".job-card-box",
            "a[href*='/job_detail/']",
        ),
        title_selectors=(
            ".job-name",
            "[class*='job-name']",
            ".job-title",
            "[class*='job-title']",
        ),
        company_selectors=(
            ".company-name",
            "[class*='company-name']",
            ".company-info",
        ),
        salary_selectors=(".salary", "[class*='salary']"),
        link_selectors=(
            "a[href*='/job_detail/']",
            ".job-name a",
            "a.job-card-left",
        ),
        jd_selectors=(
            ".job-detail-section .job-sec-text",
            ".job-detail .job-sec-text",
            ".job-detail-section",
            "[class*='job-detail']",
        ),
        hr_selectors=(
            ".boss-info .name",
            ".boss-name",
            ".job-boss-info .name",
            "[class*='boss'] [class*='name']",
        ),
        action_texts=("立即沟通", "打招呼", "投递简历", "申请职位"),
        already_action_texts=("继续沟通", "已沟通", "已投递"),
    ),
    "liepin": SiteConfig(
        key="liepin",
        display_name="猎聘",
        home_url="https://www.liepin.com/",
        search_url_template="https://www.liepin.com/zhaopin/?key={keyword}",
        allowed_domains=("liepin.com",),
        search_inputs=(
            "input[placeholder*='搜索职位']",
            "input[placeholder*='搜职位']",
            "input[placeholder*='职位、公司']",
            "input[name='key']",
            ".search-input input",
        ),
        card_selectors=(
            ".job-card-pc-container",
            ".job-list-item",
            ".job-card-container",
            "a[href*='/job/']",
        ),
        title_selectors=(
            ".job-title-box",
            ".job-title",
            "[class*='job-title']",
        ),
        company_selectors=(
            ".company-name",
            "[class*='company-name']",
            ".company-title-box",
        ),
        salary_selectors=(".job-salary", ".salary", "[class*='salary']"),
        link_selectors=(
            "a[href*='/job/']",
            ".job-title-box a",
            ".job-title a",
        ),
        jd_selectors=(
            ".job-intro-container",
            ".job-intro-content",
            ".job-intro",
            "[class*='job-intro']",
        ),
        hr_selectors=(
            ".recruiter-name",
            "[class*='recruiter'] [class*='name']",
            ".name",
        ),
        action_texts=("聊一聊", "立即沟通", "应聘", "投递简历", "申请职位"),
        already_action_texts=("继续沟通", "已沟通", "已投递", "已申请"),
    ),
}


class SafetyStop(RuntimeError):
    """页面状态不明确或触发平台安全机制时使用的“安全停止”异常。"""


class ExtractionError(RuntimeError):
    """页面结构变化导致无法可靠提取职位数据。"""


@dataclass(frozen=True)
class Job:
    """从搜索结果与详情页组合得到的最小职位数据。"""

    platform: str
    title: str
    company: str
    salary: str = "未知"
    jd: str = ""
    url: str = ""
    hr_name: str = "未知"

    @property
    def key(self) -> str:
        return make_job_key(self)

    @property
    def short_code(self) -> str:
        return self.key[:8].upper()


@dataclass(frozen=True)
class MatchResult:
    """完全在本机计算、可解释的岗位匹配结果。"""

    score: int
    matched_skills: tuple[str, ...]
    matched_roles: tuple[str, ...]
    evidence: tuple[str, ...]
    reasons: tuple[str, ...]


class GreetingProvider(Protocol):
    """招呼语提供方接口；未来可在独立模块中接入用户明确选择的服务。"""

    name: str

    def generate(
        self,
        jd_text: str,
        resume_json: Mapping[str, Any],
        *,
        max_chars: int = 100,
    ) -> str:
        """根据职位和履历返回一段招呼语。"""


def now_iso() -> str:
    """返回带本地时区、精确到秒的审计时间。"""

    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_text(value: Any) -> str:
    """统一全角/半角与空白，供展示字段清洗和防重使用。"""

    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    return re.sub(r"\s+", " ", text).strip()


def normalized_identity(value: str) -> str:
    """生成不受大小写与常见空白差异影响的身份比较文本。"""

    return re.sub(r"\s+", "", normalize_text(value)).casefold()


def validate_keyword(keyword: str) -> str:
    """限制搜索词，避免空值、控制字符和误输入超长文本。"""

    if not isinstance(keyword, str):
        raise ValueError("搜索词必须是字符串。")
    if any(unicodedata.category(ch).startswith("C") for ch in keyword):
        raise ValueError("搜索词不能包含换行或控制字符。")
    cleaned = normalize_text(keyword)
    if not cleaned:
        raise ValueError("搜索词不能为空。")
    if len(cleaned) > 60:
        raise ValueError("搜索词过长，请限制在 60 个字符以内。")
    return cleaned


def validate_resume(resume: Mapping[str, Any]) -> None:
    """在本地匹配之前验证履历结构，尽早给出可理解的错误。"""

    if not isinstance(resume, Mapping):
        raise ValueError("resume.json 顶层必须是 JSON 对象。")

    for field in ("name", "education"):
        if not isinstance(resume.get(field), str) or not resume[field].strip():
            raise ValueError(f"resume.json 的 {field} 必须是非空字符串。")

    for field in ("target_roles", "skills"):
        values = resume.get(field)
        if not isinstance(values, list) or not values:
            raise ValueError(f"resume.json 的 {field} 必须是非空数组。")
        if any(not isinstance(item, str) or not item.strip() for item in values):
            raise ValueError(f"resume.json 的 {field} 只能包含非空字符串。")

    internships = resume.get("internships")
    if not isinstance(internships, list) or not internships:
        raise ValueError("resume.json 的 internships 必须是非空数组。")
    for index, internship in enumerate(internships, start=1):
        if not isinstance(internship, Mapping):
            raise ValueError(f"第 {index} 段实习经历必须是 JSON 对象。")
        for field in ("company", "role", "highlights"):
            if not isinstance(internship.get(field), str) or not internship[field].strip():
                raise ValueError(f"第 {index} 段实习经历缺少非空字段 {field}。")


def load_resume(path: Path) -> dict[str, Any]:
    """以 UTF-8（兼容 BOM）读取并验证履历。"""

    try:
        with path.open("r", encoding="utf-8-sig") as file:
            data = json.load(file)
    except FileNotFoundError as exc:
        raise ValueError(f"找不到履历文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"履历 JSON 格式错误：{exc}") from exc
    validate_resume(data)
    return dict(data)


KEYWORD_ALIASES: dict[str, tuple[str, ...]] = {
    "AI Agent": ("ai agent", "agent", "智能体"),
    "大模型": ("大模型", "llm", "large language model"),
    "RAG": ("rag", "检索增强"),
    "Prompt": ("prompt", "提示词", "提示工程"),
    "Python": ("python",),
    "SQL": ("sql",),
    "Excel": ("excel",),
    "数据分析": ("数据分析", "数据处理", "数据清洗"),
    "工作流": ("工作流", "workflow"),
    "自动化": ("自动化", "automation"),
    "热管理": ("热管理", "热设计"),
    "热仿真": ("热仿真", "cfd", "仿真分析"),
    "暖通": ("暖通", "hvac", "建筑环境", "建环", "制冷"),
    "CAD": ("cad", "autocad"),
    "销售": ("销售", "客户开发", "商务拓展"),
    "项目管理": ("项目管理", "项目推进", "项目协调"),
}


def _contains_term(text: str, term: str) -> bool:
    """匹配中英文关键词；英文使用边界，避免把 AI 命中 paid。"""

    lowered = text.casefold()
    needle = term.casefold()
    if re.fullmatch(r"[a-z0-9+#. -]+", needle):
        pattern = rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])"
        return re.search(pattern, lowered) is not None
    return needle in lowered


def _canonical_terms(text: str) -> tuple[str, ...]:
    cleaned = normalize_text(text)
    return tuple(
        canonical
        for canonical, aliases in KEYWORD_ALIASES.items()
        if any(_contains_term(cleaned, alias) for alias in aliases)
    )


def _resume_text(resume: Mapping[str, Any]) -> str:
    parts: list[str] = [
        str(resume.get("education", "")),
        *[str(item) for item in resume.get("target_roles", [])],
        *[str(item) for item in resume.get("skills", [])],
    ]
    for internship in resume.get("internships", []):
        if isinstance(internship, Mapping):
            parts.extend(
                str(internship.get(field, ""))
                for field in ("company", "role", "highlights")
            )
    return " ".join(parts)


def calculate_local_match(
    jd_text: str,
    resume_json: Mapping[str, Any],
) -> MatchResult:
    """仅用本地规则计算 0～100 分，并返回命中项与依据。"""

    validate_resume(resume_json)
    cleaned_jd = normalize_text(jd_text)[:MAX_JD_CHARS]
    if not cleaned_jd:
        raise ValueError("职位 JD 为空，不能计算可靠的匹配分。")

    jd_terms = set(_canonical_terms(cleaned_jd))
    resume_terms = set(_canonical_terms(_resume_text(resume_json)))
    matched_skills = tuple(
        term for term in KEYWORD_ALIASES if term in jd_terms and term in resume_terms
    )

    matched_roles: list[str] = []
    for role in resume_json.get("target_roles", []):
        role_text = normalize_text(role)
        role_terms = set(_canonical_terms(role_text))
        compact_role = re.sub(r"(工程师|实习生|专员|岗位|方向)", "", role_text)
        if (role_terms & jd_terms) or (
            len(compact_role) >= 2 and compact_role.casefold() in cleaned_jd.casefold()
        ):
            matched_roles.append(role_text)

    evidence: list[str] = []
    for internship in resume_json.get("internships", []):
        internship_text = " ".join(
            normalize_text(internship.get(field, ""))
            for field in ("company", "role", "highlights")
        )
        if set(_canonical_terms(internship_text)) & set(matched_skills):
            evidence.append(
                f"{normalize_text(internship['company'])} · {normalize_text(internship['role'])}"
            )

    skill_score = min(60, len(matched_skills) * 15)
    role_score = 20 if matched_roles else 0
    evidence_score = min(15, len(evidence) * 8)
    breadth_score = 5 if len(matched_skills) >= 2 else 0
    score = min(100, skill_score + role_score + evidence_score + breadth_score)

    reasons: list[str] = []
    if matched_skills:
        reasons.append("履历与 JD 共同出现：" + "、".join(matched_skills))
    if matched_roles:
        reasons.append("目标方向命中：" + "、".join(matched_roles))
    if evidence:
        reasons.append("相关经历支撑：" + "、".join(evidence))
    if not reasons:
        reasons.append("未发现可由履历直接证明的关键词交集，建议人工复核。")

    return MatchResult(
        score=score,
        matched_skills=matched_skills,
        matched_roles=tuple(matched_roles),
        evidence=tuple(evidence),
        reasons=tuple(reasons),
    )


def classify_role_focus(jd_text: str) -> str:
    """返回不依赖候选人具体公司名称的岗位方向标签。"""

    terms = set(_canonical_terms(jd_text))
    has_ai = bool(terms & {"AI Agent", "大模型", "RAG", "Prompt"})
    has_thermal = bool(terms & {"热管理", "热仿真", "暖通"})
    if has_ai and has_thermal:
        return "AI + 热管理复合方向"
    if has_ai:
        return "AI / Agent 方向"
    if has_thermal:
        return "热管理 / 建环方向"
    return "通用方向"


def sanitize_greeting(raw_text: Any, max_chars: int = 100) -> str:
    """清理招呼语包装文本，并强制限制为指定 Unicode 字符数。"""

    if not isinstance(raw_text, str):
        raise ValueError("招呼语提供方返回的内容不是文本。")
    if max_chars < 1:
        raise ValueError("max_chars 必须大于 0。")

    text = raw_text.strip()
    text = re.sub(r"^```(?:text|markdown)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(r"^(?:招呼语|打招呼语|回复)\s*[:：]\s*", "", text)
    text = text.strip(" \t\r\n\"'“”‘’")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise ValueError("招呼语提供方返回了空内容。")

    if len(text) > max_chars:
        if max_chars == 1:
            return "…"
        text = text[: max_chars - 1].rstrip(" ，,；;、") + "…"
    return text


class LocalRuleGreetingProvider:
    """不联网、不收费、不读取密钥的确定性招呼语提供方。"""

    name = "local-rules"

    def generate(
        self,
        jd_text: str,
        resume_json: Mapping[str, Any],
        *,
        max_chars: int = 100,
    ) -> str:
        match = calculate_local_match(jd_text, resume_json)
        if match.matched_skills:
            skills = "、".join(match.matched_skills[:3])
            if match.evidence:
                raw = f"您好，我具备{skills}相关能力，并有真实实习实践，与岗位需求较匹配，期待沟通。"
            else:
                raw = f"您好，我具备{skills}相关能力，与岗位需求较匹配，希望有机会进一步沟通。"
        elif match.matched_roles:
            raw = f"您好，我的求职方向是{match.matched_roles[0]}，已认真阅读岗位要求，期待进一步沟通。"
        else:
            raw = "您好，我已认真阅读岗位要求，对该职位很感兴趣，希望有机会进一步了解并沟通。"
        return sanitize_greeting(raw, max_chars=max_chars)


def generate_greeting(
    jd_text: str,
    resume_json: Mapping[str, Any],
    *,
    provider: GreetingProvider | None = None,
    max_chars: int = 100,
) -> str:
    """通过可插拔接口生成招呼语；当前默认且唯一内置实现为本地规则。"""

    selected_provider = provider or LocalRuleGreetingProvider()
    return sanitize_greeting(
        selected_provider.generate(jd_text, resume_json, max_chars=max_chars),
        max_chars=max_chars,
    )


def canonicalize_job_url(url: str) -> tuple[str, str | None]:
    """去除追踪参数，并尽量提取平台稳定的职位 ID。"""

    if not url:
        return "", None
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = re.sub(r"/+", "/", parts.path).rstrip("/")

    stable_id: str | None = None
    for pattern in (
        r"/job_detail/([A-Za-z0-9_-]+)(?:\.html)?$",
        r"/job/([A-Za-z0-9_-]+)(?:\.shtml|\.html)?$",
    ):
        match = re.search(pattern, path, flags=re.IGNORECASE)
        if match:
            stable_id = match.group(1)
            break

    stable_query: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        if key.casefold() in {"jobid", "job_id", "id"} and value:
            stable_query.append((key.casefold(), value))
            stable_id = stable_id or value

    query = "&".join(f"{key}={value}" for key, value in sorted(stable_query))
    canonical = urlunsplit(("https", host, path, query, "")) if host else ""
    return canonical, stable_id


def make_job_key(job: Job) -> str:
    """生成稳定防重键：优先职位 ID/规范 URL，最后回退到公司+岗位。"""

    canonical_url, stable_id = canonicalize_job_url(job.url)
    if stable_id:
        identity = f"id:{normalized_identity(stable_id)}"
    elif canonical_url:
        identity = f"url:{normalized_identity(canonical_url)}"
    else:
        identity = (
            f"fallback:{normalized_identity(job.company)}|{normalized_identity(job.title)}"
        )
    material = f"{job.platform}|{identity}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class InteractionStore:
    """SQLite 沟通记录；唯一约束是防重复的最后一道保证。"""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _init_db(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    job_key TEXT NOT NULL,
                    job_url TEXT NOT NULL DEFAULT '',
                    company TEXT NOT NULL,
                    job_title TEXT NOT NULL,
                    salary TEXT NOT NULL DEFAULT '',
                    hr_name TEXT NOT NULL DEFAULT '未知',
                    greeting TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'draft', 'cancelled', 'confirmed', 'opened',
                            'sent', 'uncertain', 'failed'
                        )
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(platform, job_key)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_interactions_status "
                "ON interactions(status)"
            )

    def get(self, job: Job) -> sqlite3.Row | None:
        with closing(self._connect()) as connection, connection:
            return connection.execute(
                "SELECT * FROM interactions WHERE platform = ? AND job_key = ?",
                (job.platform, job.key),
            ).fetchone()

    def should_block(self, job: Job) -> bool:
        row = self.get(job)
        return bool(row and row["status"] in BLOCKING_STATUSES)

    def save_draft(self, job: Job, greeting: str) -> bool:
        """保存/更新草稿，但绝不覆盖可能已发生沟通的记录。"""

        timestamp = now_iso()
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO interactions (
                    platform, job_key, job_url, company, job_title, salary,
                    hr_name, greeting, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
                ON CONFLICT(platform, job_key) DO UPDATE SET
                    job_url = excluded.job_url,
                    company = excluded.company,
                    job_title = excluded.job_title,
                    salary = excluded.salary,
                    hr_name = excluded.hr_name,
                    greeting = excluded.greeting,
                    status = 'draft',
                    updated_at = excluded.updated_at
                WHERE interactions.status IN ('draft', 'cancelled', 'failed')
                """,
                (
                    job.platform,
                    job.key,
                    job.url,
                    job.company,
                    job.title,
                    job.salary,
                    job.hr_name,
                    greeting,
                    timestamp,
                    timestamp,
                ),
            )
            return cursor.rowcount > 0

    def reserve_confirmation(self, job: Job, greeting: str) -> bool:
        """原子地预留本次操作，避免并发运行造成重复沟通。"""

        connection = self._connect()
        try:
            connection.isolation_level = None
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM interactions WHERE platform = ? AND job_key = ?",
                (job.platform, job.key),
            ).fetchone()
            if row and row["status"] in BLOCKING_STATUSES:
                connection.execute("ROLLBACK")
                return False

            timestamp = now_iso()
            connection.execute(
                """
                INSERT INTO interactions (
                    platform, job_key, job_url, company, job_title, salary,
                    hr_name, greeting, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?)
                ON CONFLICT(platform, job_key) DO UPDATE SET
                    job_url = excluded.job_url,
                    company = excluded.company,
                    job_title = excluded.job_title,
                    salary = excluded.salary,
                    hr_name = excluded.hr_name,
                    greeting = excluded.greeting,
                    status = 'confirmed',
                    updated_at = excluded.updated_at
                """,
                (
                    job.platform,
                    job.key,
                    job.url,
                    job.company,
                    job.title,
                    job.salary,
                    job.hr_name,
                    greeting,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def set_status(self, job: Job, status: str) -> None:
        if status not in ALL_STATUSES:
            raise ValueError(f"非法沟通状态：{status}")
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE interactions
                SET status = ?, updated_at = ?
                WHERE platform = ? AND job_key = ?
                """,
                (status, now_iso(), job.platform, job.key),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("无法更新沟通记录：对应职位不存在。")


def respectful_delay(
    *,
    sleeper: Callable[[float], None] = time.sleep,
    rng: Callable[[float, float], float] = random.uniform,
) -> float:
    """在已确认的单次操作前温和等待 5–10 秒，不用于规避平台机制。"""

    seconds = rng(5.0, 10.0)
    print(f"已确认，操作前等待 {seconds:.1f} 秒……")
    sleeper(seconds)
    return seconds


def safe_input(prompt: str) -> str:
    """读取终端输入；无交互输入流时按“拒绝/取消”处理。"""

    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def url_is_allowed(url: str, config: SiteConfig) -> bool:
    """只允许访问配置中的招聘站点主域及其子域。"""

    host = (urlsplit(url).hostname or "").lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in config.allowed_domains)


def clean_html_text(value: str) -> str:
    """将 JSON-LD 中常见的 HTML 描述转为普通文本。"""

    without_tags = re.sub(r"<[^>]+>", " ", value)
    return normalize_text(html.unescape(without_tags))


def _find_job_posting_description(node: Any) -> str:
    """递归查找 JSON-LD 中 @type=JobPosting 的 description。"""

    if isinstance(node, Mapping):
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if any(str(item).casefold() == "jobposting" for item in types):
            description = node.get("description")
            if isinstance(description, str):
                return clean_html_text(description)
        for value in node.values():
            found = _find_job_posting_description(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_job_posting_description(item)
            if found:
                return found
    return ""


class JobSiteAdapter:
    """共享的 Playwright 站点适配器。

    该类不使用 stealth、代理、私有接口或强制点击。读取字段可以依次回退，
    但不可逆按钮必须恰好找到一个可见且文案精确匹配的候选项。
    """

    CHALLENGE_TEXTS = (
        "安全验证",
        "请完成验证",
        "滑动验证",
        "访问过于频繁",
        "异常访问",
        "请输入验证码",
        "系统检测到您的访问行为异常",
    )
    CHALLENGE_URL_PARTS = ("captcha", "verify", "challenge", "intercept")
    NO_RESULT_TEXTS = ("暂无相关职位", "没有找到相关职位", "未找到相关职位")
    LOGIN_TEXTS = ("请先登录", "登录后查看", "扫码登录", "手机登录")

    def __init__(self, config: SiteConfig, context: Any, result_page: Any):
        self.config = config
        self.context = context
        self.result_page = result_page

    @staticmethod
    def _first_visible(scope: Any, selectors: Sequence[str]) -> Any | None:
        """返回第一个可见元素；仅用于读取字段或搜索框。"""

        for selector in selectors:
            try:
                locator = scope.locator(selector)
                count = min(locator.count(), 30)
                for index in range(count):
                    candidate = locator.nth(index)
                    if candidate.is_visible():
                        return candidate
            except Exception:
                # 某个回退选择器失效不应中断全部提取，继续尝试下一项。
                continue
        return None

    @classmethod
    def _first_text(cls, scope: Any, selectors: Sequence[str]) -> str:
        locator = cls._first_visible(scope, selectors)
        if locator is None:
            return ""
        try:
            return normalize_text(locator.inner_text(timeout=1_500))
        except Exception:
            return ""

    @classmethod
    def _first_attr(cls, scope: Any, selectors: Sequence[str], name: str) -> str:
        locator = cls._first_visible(scope, selectors)
        if locator is None:
            return ""
        try:
            return normalize_text(locator.get_attribute(name, timeout=1_500))
        except Exception:
            return ""

    @staticmethod
    def _body_text(page: Any, timeout: int = 2_000) -> str:
        try:
            return page.locator("body").inner_text(timeout=timeout)
        except Exception:
            return ""

    def challenge_detected(self, page: Any) -> bool:
        """只检测安全页面；不尝试识别或处理验证码。"""

        lowered_url = (page.url or "").casefold()
        if any(part in lowered_url for part in self.CHALLENGE_URL_PARTS):
            return True
        body = self._body_text(page)[:8_000]
        return any(text in body for text in self.CHALLENGE_TEXTS)

    def stop_on_challenge(self, page: Any) -> None:
        if self.challenge_detected(page):
            raise SafetyStop(
                "检测到验证码、访问限制或安全验证。程序已停止自动操作；"
                "请在浏览器中按平台正常流程手动处理，切勿尝试绕过。"
            )

    def open_home_and_login_gate(self, first_run: bool) -> None:
        """打开首页并由用户手动确认登录；Cookie 会保存在 persistent context。"""

        self.result_page.goto(
            self.config.home_url,
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        self.result_page.bring_to_front()
        if first_run:
            print(
                f"\n首次使用 {self.config.display_name}：请在浏览器中手动扫码/登录。"
            )
        else:
            print(
                f"\n已加载 {self.config.display_name} 的本地登录状态。"
                "若登录已过期，请在浏览器中手动重新登录。"
            )
        print("程序不会填写账号、识别验证码或绕过平台安全机制。")
        answer = safe_input("确认浏览器中已登录后按 Enter；输入 q 退出：")
        if answer.casefold() == "q":
            raise KeyboardInterrupt
        self.stop_on_challenge(self.result_page)

    def search(self, keyword: str) -> None:
        """优先使用可见搜索框；找不到时回退到站点公开搜索 URL。"""

        search_input = self._first_visible(self.result_page, self.config.search_inputs)
        if search_input is not None:
            try:
                search_input.fill(keyword)
                search_input.press("Enter")
            except Exception as exc:
                raise ExtractionError(f"搜索框存在但无法输入：{exc}") from exc
        else:
            search_url = self.config.search_url_template.format(
                keyword=quote(keyword, safe="")
            )
            self.result_page.goto(
                search_url,
                wait_until="domcontentloaded",
                timeout=30_000,
            )

        self.wait_for_results_state()

    def _visible_count(self, scope: Any, selector: str) -> int:
        try:
            locator = scope.locator(selector)
            count = min(locator.count(), 100)
            return sum(1 for index in range(count) if locator.nth(index).is_visible())
        except Exception:
            return 0

    def _selected_card_locator(self) -> tuple[Any, str] | None:
        for selector in self.config.card_selectors:
            if self._visible_count(self.result_page, selector) > 0:
                return self.result_page.locator(selector), selector
        return None

    def wait_for_results_state(self, timeout_seconds: float = 20.0) -> None:
        """等待职位卡片、空结果、登录提示或安全页中的任一明确状态。"""

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            self.stop_on_challenge(self.result_page)
            if self._selected_card_locator() is not None:
                return
            body = self._body_text(self.result_page)[:8_000]
            if any(text in body for text in self.NO_RESULT_TEXTS):
                raise ExtractionError("搜索完成，但没有找到相关职位。")
            if any(text in body for text in self.LOGIN_TEXTS):
                raise SafetyStop("页面要求重新登录，请手动登录后重新运行。")
            self.result_page.wait_for_timeout(500)
        raise ExtractionError(
            "等待搜索结果超时。页面结构可能已更新，请检查 SITE_CONFIGS 中的选择器。"
        )

    def _extract_card(self, card: Any) -> Job | None:
        title = self._first_text(card, self.config.title_selectors)
        company = self._first_text(card, self.config.company_selectors)
        salary = self._first_text(card, self.config.salary_selectors) or "未知"

        # 卡片本身偶尔就是链接；否则在卡片内部找职位详情链接。
        try:
            href = normalize_text(card.get_attribute("href", timeout=800))
        except Exception:
            href = ""
        href = href or self._first_attr(card, self.config.link_selectors, "href")

        if not title or not company or not href:
            return None
        absolute_url = urljoin(self.result_page.url, href)
        if not url_is_allowed(absolute_url, self.config):
            return None

        return Job(
            platform=self.config.key,
            title=title,
            company=company,
            salary=salary,
            url=absolute_url,
        )

    def collect_jobs(self, limit: int) -> list[Job]:
        """有界读取搜索结果并按稳定职位键去重，不做无限滚动。"""

        selected = self._selected_card_locator()
        if selected is None:
            raise ExtractionError("未找到职位卡片，页面结构可能已更新。")
        cards, selector_used = selected
        print(f"职位卡片选择器命中：{selector_used}")

        jobs: list[Job] = []
        seen: set[str] = set()
        count = min(cards.count(), max(limit * 3, limit), 100)
        for index in range(count):
            if len(jobs) >= limit:
                break
            card = cards.nth(index)
            try:
                if not card.is_visible():
                    continue
                job = self._extract_card(card)
            except Exception:
                job = None
            if job is None or job.key in seen:
                continue
            seen.add(job.key)
            jobs.append(job)

        if not jobs:
            raise ExtractionError(
                "找到了页面元素，但无法可靠读取岗位名、公司或详情链接。"
                "请按当前页面更新 SITE_CONFIGS。"
            )
        return jobs

    def _extract_json_ld_jd(self, page: Any) -> str:
        try:
            scripts = page.locator("script[type='application/ld+json']").all_text_contents()
        except Exception:
            return ""
        for script in scripts:
            try:
                payload = json.loads(script)
            except (TypeError, json.JSONDecodeError):
                continue
            description = _find_job_posting_description(payload)
            if description:
                return description
        return ""

    def _extract_body_jd(self, page: Any) -> str:
        """最后回退：仅截取“职位描述/岗位职责”附近内容，不发送整个页面。"""

        body = self._body_text(page, timeout=3_000)
        if not body:
            return ""
        body = body.replace("\r\n", "\n").replace("\r", "\n")
        starts = ("职位描述", "岗位职责", "职位职责", "工作内容", "职位要求")
        ends = ("公司介绍", "工商信息", "工作地点", "相似职位", "安全提示")
        positions = [body.find(label) for label in starts if body.find(label) >= 0]
        if not positions:
            return ""
        start = min(positions)
        end_positions = [body.find(label, start + 1) for label in ends]
        end_positions = [position for position in end_positions if position > start]
        end = min(end_positions) if end_positions else min(len(body), start + MAX_JD_CHARS)
        return normalize_text(body[start:end])[:MAX_JD_CHARS]

    @staticmethod
    def _jd_is_usable(jd: str) -> bool:
        if len(jd) < 20:
            return False
        hints = (
            "职责",
            "要求",
            "经验",
            "负责",
            "能力",
            "岗位",
            "工作",
            "任职",
        )
        return any(word in jd for word in hints)

    def load_job_detail(self, job: Job) -> Job:
        """在独立标签页读取 JD/HR，完成后关闭，保留搜索结果页。"""

        if not job.url or not url_is_allowed(job.url, self.config):
            raise ExtractionError("职位详情链接为空或不属于目标招聘站点。")

        page = self.context.new_page()
        try:
            page.goto(job.url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(1_000)
            self.stop_on_challenge(page)

            jd = self._extract_json_ld_jd(page)
            if not self._jd_is_usable(jd):
                jd = self._first_text(page, self.config.jd_selectors)
            if not self._jd_is_usable(jd):
                jd = self._extract_body_jd(page)
            jd = normalize_text(jd)[:MAX_JD_CHARS]
            if not self._jd_is_usable(jd):
                raise ExtractionError(
                    "未能可靠提取职位 JD；为避免错误匹配，本岗位已跳过。"
                )

            hr_name = self._first_text(page, self.config.hr_selectors) or "未知"
            return replace(job, jd=jd, hr_name=hr_name)
        finally:
            try:
                page.close()
            except Exception:
                pass

    def _page_matches_job(self, page: Any, job: Job) -> bool:
        """点击前核对 URL、岗位名和公司，防止确认期间页面已切换。"""

        if not url_is_allowed(page.url, self.config):
            return False
        body_identity = normalized_identity(self._body_text(page, timeout=3_000))
        return (
            normalized_identity(job.title) in body_identity
            and normalized_identity(job.company) in body_identity
        )

    def _visible_exact_role_candidates(
        self, page: Any, texts: Sequence[str]
    ) -> list[tuple[str, Any]]:
        """为不可逆操作收集语义角色和精确文案都匹配的可见元素。"""

        candidates: list[tuple[str, Any]] = []
        for label in texts:
            pattern = re.compile(rf"^{re.escape(label)}$")
            for role in ("button", "link"):
                try:
                    locator = page.get_by_role(role, name=pattern)
                    count = min(locator.count(), 20)
                    for index in range(count):
                        item = locator.nth(index)
                        if item.is_visible() and item.is_enabled():
                            candidates.append((label, item))
                except Exception:
                    continue
        return candidates

    def find_single_action(self, page: Any) -> tuple[str, Any]:
        """只接受恰好一个允许文案的按钮；不存在或有歧义都安全停止。"""

        already = self._visible_exact_role_candidates(page, self.config.already_action_texts)
        if already:
            labels = "、".join(sorted({label for label, _ in already}))
            raise SafetyStop(f"页面显示“{labels}”，该岗位可能已经沟通/投递。")

        candidates = self._visible_exact_role_candidates(page, self.config.action_texts)
        if len(candidates) != 1:
            raise SafetyStop(
                f"期望恰好 1 个沟通/投递入口，实际找到 {len(candidates)} 个。"
                "为避免误点，程序不会猜测。"
            )
        return candidates[0]

    def open_action_page(self, job: Job) -> tuple[Any, str]:
        """打开、校验岗位页，并返回唯一入口按钮的文案（尚未点击）。"""

        if not job.url or not url_is_allowed(job.url, self.config):
            raise SafetyStop("职位链接不在允许域名内，拒绝打开操作入口。")
        page = self.context.new_page()
        try:
            page.goto(job.url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(1_000)
            self.stop_on_challenge(page)
            if not self._page_matches_job(page, job):
                raise SafetyStop("详情页的岗位名/公司与待确认记录不一致。")
            label, _ = self.find_single_action(page)
            page.bring_to_front()
            return page, label
        except Exception:
            try:
                page.close()
            except Exception:
                pass
            raise

    def click_action_once(self, page: Any, job: Job, expected_label: str) -> None:
        """确认后的唯一不可逆点击；等待期间页面变化会导致停止。"""

        self.stop_on_challenge(page)
        if not self._page_matches_job(page, job):
            raise SafetyStop("等待期间页面内容发生变化，已取消点击。")
        label, locator = self.find_single_action(page)
        if label != expected_label:
            raise SafetyStop(
                f"等待期间按钮从“{expected_label}”变为“{label}”，已取消点击。"
            )

        # 禁止 force=True、坐标点击或 JavaScript 点击，保留 Playwright 的可操作性检查。
        locator.click(timeout=8_000)
        page.wait_for_timeout(1_000)
        if self.challenge_detected(page):
            raise SafetyStop(
                "点击后出现平台安全验证。结果可能不确定，已停止且不会自动重试。"
            )


def print_job_preview(
    job: Job,
    greeting: str,
    match: MatchResult,
    index: int,
    total: int,
) -> None:
    """在确认前显示用户需要人工核对的全部关键字段。"""

    print("\n" + "=" * 72)
    print(f"岗位 {index}/{total}  |  短码 {job.short_code}")
    print(f"平台：{SITE_CONFIGS[job.platform].display_name}")
    print(f"公司：{job.company}")
    print(f"岗位：{job.title}")
    print(f"薪资：{job.salary}")
    print(f"HR：{job.hr_name}")
    print(f"链接：{job.url}")
    print(f"本地匹配分：{match.score}/100")
    print("匹配依据：" + "；".join(match.reasons))
    print(f"招呼语（{len(greeting)}/100 字）：{greeting}")
    print("=" * 72)


def application_entry(
    job: Job,
    match: MatchResult,
    greeting: str,
) -> dict[str, Any]:
    """生成不包含完整履历的清单记录。"""

    return {
        "platform": job.platform,
        "company": job.company,
        "title": job.title,
        "salary": job.salary,
        "hr_name": job.hr_name,
        "url": job.url,
        "score": match.score,
        "matched_skills": list(match.matched_skills),
        "matched_roles": list(match.matched_roles),
        "reasons": list(match.reasons),
        "greeting": greeting,
    }


def write_application_list(path: Path, entries: Sequence[Mapping[str, Any]]) -> None:
    """把按分数排序后的候选投递清单写入本地 JSON。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(entries, key=lambda item: int(item.get("score", 0)), reverse=True)
    payload = {
        "generated_at": now_iso(),
        "generator": "local-rules",
        "count": len(ordered),
        "jobs": ordered,
    }
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


def load_offline_jobs(path: Path, platform: str) -> list[Job]:
    """读取用户提供的本地职位 JSON，便于零联网测试完整匹配流程。"""

    try:
        with path.open("r", encoding="utf-8-sig") as file:
            payload = json.load(file)
    except FileNotFoundError as exc:
        raise ValueError(f"找不到离线职位文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"离线职位 JSON 格式错误：{exc}") from exc

    rows = payload.get("jobs") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list) or not rows:
        raise ValueError("离线职位文件必须是非空数组，或包含非空 jobs 数组。")

    jobs: list[Job] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise ValueError(f"离线职位第 {index} 项必须是 JSON 对象。")
        required = {}
        for field in ("title", "company", "jd"):
            value = normalize_text(row.get(field, ""))
            if not value:
                raise ValueError(f"离线职位第 {index} 项缺少非空字段 {field}。")
            required[field] = value
        jobs.append(
            Job(
                platform=platform,
                title=required["title"],
                company=required["company"],
                salary=normalize_text(row.get("salary", "")) or "未知",
                jd=required["jd"][:MAX_JD_CHARS],
                url=normalize_text(row.get("url", "")),
                hr_name=normalize_text(row.get("hr_name", "")) or "未知",
            )
        )
    return jobs


def evaluate_job(
    job: Job,
    resume: Mapping[str, Any],
) -> tuple[MatchResult, str]:
    """对一个职位执行本地评分并生成招呼语。"""

    matching_text = f"{job.title}\n{job.jd}"
    match = calculate_local_match(matching_text, resume)
    greeting = generate_greeting(matching_text, resume)
    return match, greeting


def run_offline(args: argparse.Namespace, resume: Mapping[str, Any]) -> int:
    """不启动浏览器的离线验收入口。"""

    if args.enable_actions:
        raise ValueError("离线模式不能使用 --enable-actions。")
    jobs = load_offline_jobs(args.offline_jobs.resolve(), args.platform)[: args.limit]
    accepted: list[dict[str, Any]] = []
    for index, job in enumerate(jobs, start=1):
        match, greeting = evaluate_job(job, resume)
        print_job_preview(job, greeting, match, index, len(jobs))
        if match.score >= args.min_score:
            accepted.append(application_entry(job, match, greeting))
        else:
            print(f"低于阈值 {args.min_score} 分，未进入投递清单。")
    write_application_list(args.output_list.resolve(), accepted)
    print(f"\n离线处理完成，投递清单保存在：{args.output_list.resolve()}")
    print("离线模式未打开网页，也未执行任何沟通或投递操作。")
    return 0


def review_and_maybe_open_action(
    adapter: JobSiteAdapter,
    store: InteractionStore,
    job: Job,
    greeting: str,
    *,
    enable_actions: bool,
) -> None:
    """预览草稿，并在满足全部安全条件时允许一次入口点击。"""

    if not store.save_draft(job, greeting):
        print("记录已被另一进程更新为不可重复状态，本岗位跳过。")
        return

    if not enable_actions:
        print("当前为默认预览模式：没有点击任何沟通/投递按钮。")
        return

    action_page: Any | None = None
    try:
        action_page, action_label = adapter.open_action_page(job)
        confirmation = f"确认 {job.short_code}"
        print(f"\n浏览器已打开并复核岗位。下一步将只点击一次“{action_label}”入口。")
        print("若点击后出现聊天框、发送按钮或投递确认框，必须由你在浏览器中手动完成。")
        answer = safe_input(f"若确定继续，请完整输入“{confirmation}”；其他输入均取消：")
        if answer != confirmation:
            store.set_status(job, "cancelled")
            print("已取消；未点击沟通/投递入口。")
            return

        # 先以数据库事务预留本次沟通，再等待并重新校验页面，防止并发重复与页面切换。
        if not store.reserve_confirmation(job, greeting):
            print("数据库显示该岗位可能已处理，本次不点击。")
            return

        respectful_delay()
        try:
            adapter.click_action_once(action_page, job, action_label)
        except Exception:
            # Playwright 超时可能发生在页面已收到点击之后，不能武断地标为“失败并可重试”。
            store.set_status(job, "uncertain")
            raise

        store.set_status(job, "opened")
        action_page.bring_to_front()
        print("\n已点击一次入口。程序不会继续点击任何“发送/确认投递”按钮。")
        print(f"请在浏览器中人工复核、粘贴并发送：{greeting}")

        sent_confirmation = f"已发送 {job.short_code}"
        sent_answer = safe_input(
            f"仅在你亲眼确认发送/投递成功后输入“{sent_confirmation}”："
        )
        if sent_answer == sent_confirmation:
            store.set_status(job, "sent")
            print("已记录为 sent，后续运行将自动防重。")
        else:
            store.set_status(job, "uncertain")
            print(
                "未收到成功确认，已记录为 uncertain 并阻止自动重试；"
                "请先在平台上核实实际状态。"
            )
    finally:
        if action_page is not None:
            try:
                action_page.close()
            except Exception:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Boss直聘/猎聘本地岗位匹配与招呼语助手。默认只预览，不会点击沟通或投递。"
        )
    )
    parser.add_argument(
        "--platform",
        required=True,
        choices=sorted(SITE_CONFIGS),
        help="招聘平台：boss 或 liepin",
    )
    parser.add_argument(
        "--keyword",
        help="职位搜索词，例如：AI Agent、热管理工程师；省略时交互输入",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help=f"本次最多处理的职位数，默认 5，上限 {MAX_RESULTS}",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=DEFAULT_MIN_SCORE,
        help=f"进入投递清单的最低本地匹配分，默认 {DEFAULT_MIN_SCORE}",
    )
    parser.add_argument(
        "--resume",
        type=Path,
        default=DEFAULT_RESUME_PATH,
        help="履历 JSON 路径",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="SQLite 沟通记录路径",
    )
    parser.add_argument(
        "--profile-root",
        type=Path,
        default=DEFAULT_PROFILE_ROOT,
        help="Playwright 持久化登录目录根路径",
    )
    parser.add_argument(
        "--offline-jobs",
        type=Path,
        help="从本地职位 JSON 运行完整匹配流程，不启动浏览器",
    )
    parser.add_argument(
        "--output-list",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="投递清单 JSON 输出路径",
    )
    parser.add_argument(
        "--enable-actions",
        action="store_true",
        help=(
            "开放逐岗位确认后的单次入口点击。未传入此参数时始终为只读预览模式。"
        ),
    )
    return parser


def run(args: argparse.Namespace) -> int:
    if not 1 <= args.limit <= MAX_RESULTS:
        raise ValueError(f"--limit 必须在 1 到 {MAX_RESULTS} 之间。")
    if not 0 <= args.min_score <= 100:
        raise ValueError("--min-score 必须在 0 到 100 之间。")
    if args.offline_jobs is None:
        raise SafetyStop(
            "BOSS直聘/猎聘实时页面模式已停用：当前平台规则不允许本工具进行"
            "自动抓取或拟人访问。请在 Streamlit 的“真实岗位工具”中打开官网，"
            "再粘贴完整 JD 或上传本人保存的岗位文件。"
        )

    resume = load_resume(args.resume.resolve())
    if args.offline_jobs is not None:
        return run_offline(args, resume)

    keyword = args.keyword
    if keyword is None:
        keyword = safe_input("请输入职位搜索词：")
    keyword = validate_keyword(keyword)
    store = InteractionStore(args.db.resolve())
    config = SITE_CONFIGS[args.platform]

    profile_dir = (args.profile_root.resolve() / config.key)
    first_run = not profile_dir.exists() or not any(profile_dir.iterdir())
    profile_dir.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "尚未安装 Playwright。请安装 requirements.txt，并运行 playwright install chromium。"
        ) from exc

    print("\n安全说明：")
    print("- 仅访问你已登录且有权查看的职位页面，并请自行核对平台现行规则。")
    print("- 匹配评分与招呼语均在本机生成，不调用 OpenAI API，也不读取 API Key。")
    print("- 浏览器登录目录和 SQLite 数据库含敏感信息，请只保存在本机。")
    if args.enable_actions:
        print("- 已开放操作模式，但每个岗位仍需单独输入精确确认口令。")
    else:
        print("- 当前为默认预览模式，不会点击任何沟通/投递入口。")

    with sync_playwright() as playwright:
        application_entries: list[dict[str, Any]] = []
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                locale="zh-CN",
                viewport={"width": 1440, "height": 900},
                accept_downloads=False,
            )
        except Exception as exc:
            raise RuntimeError(
                "无法启动持久化 Chromium。请确认已执行 playwright install chromium，"
                "并确保没有另一个程序正在占用同一 profile 目录。"
            ) from exc

        try:
            context.set_default_timeout(8_000)
            page = context.pages[0] if context.pages else context.new_page()
            adapter = JobSiteAdapter(config, context, page)
            adapter.open_home_and_login_gate(first_run)
            adapter.search(keyword)
            jobs = adapter.collect_jobs(args.limit)
            print(f"\n已读取 {len(jobs)} 个候选岗位，开始逐个提取 JD 与生成草稿。")

            for index, basic_job in enumerate(jobs, start=1):
                if store.should_block(basic_job):
                    row = store.get(basic_job)
                    status = row["status"] if row else "未知"
                    print(
                        f"\n[{index}/{len(jobs)}] 跳过已处理岗位："
                        f"{basic_job.company} / {basic_job.title}（{status}）"
                    )
                    continue

                print(
                    f"\n[{index}/{len(jobs)}] 读取详情："
                    f"{basic_job.company} / {basic_job.title}"
                )
                try:
                    job = adapter.load_job_detail(basic_job)
                except ExtractionError as exc:
                    print(f"跳过本岗位：{exc}")
                    continue

                try:
                    match, greeting = evaluate_job(job, resume)
                except Exception as exc:
                    print(f"本地匹配失败，本岗位不会执行任何操作：{exc}")
                    continue

                print_job_preview(job, greeting, match, index, len(jobs))
                if match.score < args.min_score:
                    print(f"低于阈值 {args.min_score} 分，未进入投递清单，也不会执行操作。")
                    continue
                application_entries.append(application_entry(job, match, greeting))
                review_and_maybe_open_action(
                    adapter,
                    store,
                    job,
                    greeting,
                    enable_actions=args.enable_actions,
                )
        finally:
            context.close()

    write_application_list(args.output_list.resolve(), application_entries)
    print(f"投递清单保存在：{args.output_list.resolve()}")
    print(f"\n处理完成。沟通记录保存在：{args.db.resolve()}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\n用户取消，程序已安全退出。")
        return 130
    except SafetyStop as exc:
        print(f"\n安全停止：{exc}", file=sys.stderr)
        return 3
    except (ValueError, RuntimeError, ExtractionError) as exc:
        print(f"\n错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
