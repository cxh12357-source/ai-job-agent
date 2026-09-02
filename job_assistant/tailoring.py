from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any


class TailoringError(ValueError):
    """Raised when a resume tailoring draft cannot be produced safely."""


@dataclass(frozen=True, slots=True)
class TailoringChange:
    """One transparent, reversible change made to the draft."""

    kind: str
    section: str
    detail: str


@dataclass(frozen=True, slots=True)
class KeywordCoverage:
    """Evidence for one skill or qualification mentioned in the JD."""

    keyword: str
    covered: bool
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TailoredSection:
    """A resume section after safe ordering and formatting changes."""

    heading: str
    lines: tuple[str, ...]
    relevance_score: int
    matched_keywords: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TailoringResult:
    """A source-faithful resume draft plus an explicit tailoring report."""

    job_title: str
    company: str
    tailored_text: str
    sections: tuple[TailoredSection, ...]
    changes: tuple[TailoringChange, ...]
    keyword_coverage: tuple[KeywordCoverage, ...]
    gaps: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def covered_keywords(self) -> tuple[str, ...]:
        return tuple(item.keyword for item in self.keyword_coverage if item.covered)

    @property
    def missing_keywords(self) -> tuple[str, ...]:
        return tuple(item.keyword for item in self.keyword_coverage if not item.covered)

    @property
    def coverage_ratio(self) -> float:
        if not self.keyword_coverage:
            return 0.0
        return len(self.covered_keywords) / len(self.keyword_coverage)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation for UI/API use."""

        return {
            "job_title": self.job_title,
            "company": self.company,
            "tailored_text": self.tailored_text,
            "sections": [asdict(section) for section in self.sections],
            "changes": [asdict(change) for change in self.changes],
            "keyword_coverage": [
                asdict(coverage) for coverage in self.keyword_coverage
            ],
            "covered_keywords": list(self.covered_keywords),
            "missing_keywords": list(self.missing_keywords),
            "coverage_ratio": round(self.coverage_ratio, 4),
            "gaps": list(self.gaps),
            "warnings": list(self.warnings),
        }

    def to_markdown(self) -> str:
        """Create a downloadable text preview with the report kept separate."""

        target = " / ".join(part for part in (self.company, self.job_title) if part)
        title = f"# 简历定制草稿：{target}" if target else "# 简历定制草稿"
        covered = "、".join(self.covered_keywords) or "未识别到"
        missing = "、".join(self.missing_keywords) or "无"
        change_lines = [
            f"- {change.detail}" for change in self.changes
        ] or ["- 未改变原简历顺序或格式"]
        gap_lines = [f"- {gap}" for gap in self.gaps] or ["- 未发现明确缺口"]
        warning_lines = [f"- {warning}" for warning in self.warnings]
        warning_block = "\n".join(warning_lines) or "- 无"
        return "\n".join(
            (
                title,
                "",
                "## 关键词覆盖",
                "",
                f"- 已覆盖：{covered}",
                f"- 未覆盖：{missing}",
                f"- 覆盖率：{self.coverage_ratio:.0%}",
                "",
                "## 变更说明",
                "",
                *change_lines,
                "",
                "## 缺口提示（不要在没有事实依据时写入简历）",
                "",
                *gap_lines,
                "",
                "## 风险提示",
                "",
                warning_block,
                "",
                "## 定制后的简历正文",
                "",
                self.tailored_text,
                "",
            )
        )


@dataclass(frozen=True, slots=True)
class _KeywordSpec:
    label: str
    aliases: tuple[str, ...]


# The list is deliberately evidence-oriented: every term must occur in the JD
# before it can affect ordering or be reported as a gap.  Aliases only establish
# equivalence; they are never copied into the resume body.
_KEYWORDS = (
    _KeywordSpec("AI", ("artificial intelligence", "人工智能")),
    _KeywordSpec("AI Agent", ("ai agent", "智能体", "agentic ai")),
    _KeywordSpec("LLM", ("large language model", "large language models", "大模型")),
    _KeywordSpec("RAG", ("retrieval augmented generation", "检索增强生成")),
    _KeywordSpec("Prompt Engineering", ("prompt engineering", "提示词工程")),
    _KeywordSpec("Machine Learning", ("machine learning", "机器学习")),
    _KeywordSpec("Deep Learning", ("deep learning", "深度学习")),
    _KeywordSpec("Python", ("python",)),
    _KeywordSpec("SQL", ("sql",)),
    _KeywordSpec("Excel", ("excel",)),
    _KeywordSpec("Power BI", ("power bi", "powerbi")),
    _KeywordSpec("Tableau", ("tableau",)),
    _KeywordSpec("pandas", ("pandas",)),
    _KeywordSpec("NumPy", ("numpy",)),
    _KeywordSpec("PyTorch", ("pytorch",)),
    _KeywordSpec("TensorFlow", ("tensorflow",)),
    _KeywordSpec("Java", ("java",)),
    _KeywordSpec("JavaScript", ("javascript",)),
    _KeywordSpec("TypeScript", ("typescript",)),
    _KeywordSpec("C++", ("c++",)),
    _KeywordSpec("Git", ("git", "github", "gitlab")),
    _KeywordSpec("Linux", ("linux", "unix")),
    _KeywordSpec("Docker", ("docker",)),
    _KeywordSpec("Kubernetes", ("kubernetes", "k8s")),
    _KeywordSpec("AWS", ("aws", "amazon web services")),
    _KeywordSpec("Azure", ("azure",)),
    _KeywordSpec("GCP", ("gcp", "google cloud platform")),
    _KeywordSpec("API", ("api", "rest api", "restful api", "接口开发")),
    _KeywordSpec(
        "Data Analysis",
        ("data analysis", "data analytics", "data cleaning", "statistical analysis", "数据分析", "数据清洗"),
    ),
    _KeywordSpec("Data Visualization", ("data visualization", "数据可视化", "可视化")),
    _KeywordSpec("Automation", ("automation", "workflow automation", "自动化")),
    _KeywordSpec("Statistics", ("statistics", "statistical", "统计分析", "统计学")),
    _KeywordSpec("Thermal Management", ("thermal management", "热管理")),
    _KeywordSpec("Heat Transfer", ("heat transfer", "传热", "传热学")),
    _KeywordSpec("Fluid Mechanics", ("fluid mechanics", "流体力学")),
    _KeywordSpec("CFD", ("cfd", "computational fluid dynamics", "计算流体力学")),
    _KeywordSpec("Fluent", ("ansys fluent", "fluent")),
    _KeywordSpec("STAR-CCM+", ("star-ccm+", "star ccm+")),
    _KeywordSpec("MATLAB", ("matlab",)),
    _KeywordSpec("Simulink", ("simulink",)),
    _KeywordSpec("SolidWorks", ("solidworks", "solid works")),
    _KeywordSpec("AutoCAD", ("autocad", "auto cad")),
    _KeywordSpec("CAD", ("cad", "computer-aided design", "计算机辅助设计")),
    _KeywordSpec("CAE", ("cae", "computer-aided engineering", "计算机辅助工程")),
    _KeywordSpec("Manufacturing", ("manufacturing", "智能制造", "制造工程")),
    _KeywordSpec("Process Engineering", ("process engineering", "process engineer", "工艺工程")),
    _KeywordSpec("Testing", ("testing", "test engineer", "测试工程")),
    _KeywordSpec("Quality", ("quality engineering", "quality control", "质量工程", "质量管理")),
    _KeywordSpec("NPI", ("npi", "new product introduction", "新产品导入")),
    _KeywordSpec("DFM", ("dfm", "design for manufacturing", "design for manufacturability")),
    _KeywordSpec("Project Management", ("project management", "项目管理")),
    _KeywordSpec("Product Design", ("product design", "产品设计")),
    _KeywordSpec("Sales", ("sales", "销售")),
    _KeywordSpec("Customer Communication", ("customer communication", "client communication", "客户沟通")),
    _KeywordSpec("English", ("english", "英语")),
)


_SECTION_ALIASES = {
    "个人信息": {"个人信息", "基本信息", "联系方式", "contact", "contact information"},
    "求职目标": {"求职目标", "求职意向", "职业目标", "objective", "career objective"},
    "个人简介": {"个人简介", "自我评价", "职业概述", "summary", "profile", "professional summary"},
    "工作经历": {
        "工作经历", "实习经历", "职业经历", "experience", "work experience",
        "internship experience", "professional experience", "employment",
    },
    "项目经历": {"项目经历", "项目经验", "projects", "project experience"},
    "教育经历": {"教育经历", "教育背景", "education"},
    "技能": {"技能", "专业技能", "技能证书", "skills", "technical skills", "core competencies"},
    "证书": {"证书", "证书荣誉", "资格证书", "certifications", "certificates"},
    "获奖经历": {"获奖经历", "荣誉奖项", "荣誉与奖励", "awards", "honors"},
    "校园经历": {"校园经历", "社团经历", "社会实践", "activities", "campus experience"},
    "语言能力": {"语言能力", "语言", "languages", "language skills"},
}

_HEADING_LOOKUP = {
    unicodedata.normalize("NFKC", alias).casefold(): canonical
    for canonical, aliases in _SECTION_ALIASES.items()
    for alias in aliases
}

_BULLET_RE = re.compile(r"^\s*(?:[-*•·●▪◦]|\d+[.)、])\s*(.+?)\s*$")
_LATIN_TOKEN_RE = re.compile(r"^[a-z0-9+#.]+(?:[ -][a-z0-9+#.]+)*$")


def _norm(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text)).casefold()
    return re.sub(r"\s+", " ", value).strip()


def _contains(text: str, term: str) -> bool:
    normalized_text = _norm(text)
    normalized_term = _norm(term)
    if not normalized_term:
        return False
    if _LATIN_TOKEN_RE.fullmatch(normalized_term):
        pattern = rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])"
        return re.search(pattern, normalized_text) is not None
    return normalized_term in normalized_text


def _spec_present(text: str, spec: _KeywordSpec) -> bool:
    return any(_contains(text, alias) for alias in spec.aliases)


def _jd_keywords(job_description: str) -> tuple[_KeywordSpec, ...]:
    found = [spec for spec in _KEYWORDS if _spec_present(job_description, spec)]
    return tuple(
        sorted(
            found,
            key=lambda spec: min(
                index
                for alias in spec.aliases
                if (index := _norm(job_description).find(_norm(alias))) >= 0
            ),
        )
    )


def _heading(line: str) -> str | None:
    candidate = re.sub(r"^#{1,6}\s*", "", line.strip())
    candidate = candidate.rstrip(":：").strip()
    return _HEADING_LOOKUP.get(_norm(candidate))


def _clean_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _parse_sections(lines: list[str]) -> list[tuple[str, str, list[str], int]]:
    sections: list[tuple[str, str, list[str], int]] = []
    current_heading = ""
    current_canonical = "个人信息"
    current_lines: list[str] = []
    start_index = 0

    for index, line in enumerate(lines):
        canonical = _heading(line)
        if canonical is None:
            current_lines.append(line)
            continue
        if current_lines or current_heading:
            sections.append(
                (current_heading, current_canonical, current_lines, start_index)
            )
        current_heading = line
        current_canonical = canonical
        current_lines = []
        start_index = index

    if current_lines or current_heading:
        sections.append((current_heading, current_canonical, current_lines, start_index))
    return sections


def _line_matches(line: str, keywords: tuple[_KeywordSpec, ...]) -> tuple[str, ...]:
    return tuple(spec.label for spec in keywords if _spec_present(line, spec))


def _reorder_bullet_runs(
    heading: str,
    lines: list[str],
    keywords: tuple[_KeywordSpec, ...],
) -> tuple[list[str], list[TailoringChange]]:
    result = list(lines)
    changes: list[TailoringChange] = []
    index = 0
    while index < len(result):
        if _BULLET_RE.match(result[index]) is None:
            index += 1
            continue
        end = index
        run: list[tuple[int, str, str, int]] = []
        while end < len(result):
            match = _BULLET_RE.match(result[end])
            if match is None:
                break
            content = match.group(1).strip()
            run.append((len(_line_matches(content, keywords)), content, result[end], end))
            end += 1

        ordered = sorted(run, key=lambda item: (-item[0], item[3]))
        normalized = [f"- {item[1]}" for item in ordered]
        original_contents = [item[1] for item in run]
        ordered_contents = [item[1] for item in ordered]
        if ordered_contents != original_contents:
            changes.append(
                TailoringChange(
                    kind="reorder_bullets",
                    section=heading or "简历正文",
                    detail=f"在“{heading or '简历正文'}”中将与 JD 更相关的原有要点前置",
                )
            )
        if normalized != [item[2] for item in run]:
            changes.append(
                TailoringChange(
                    kind="normalize_bullets",
                    section=heading or "简历正文",
                    detail=f"统一“{heading or '简历正文'}”的项目符号格式，未改变事实内容",
                )
            )
        result[index:end] = normalized
        index = end
    return result, changes


def _requirement_warnings(job_description: str, resume_text: str) -> list[str]:
    warnings: list[str] = []
    jd = _norm(job_description)
    resume = _norm(resume_text)

    experience_matches = [
        int(value)
        for pattern in (
            r"(\d+)\s*\+\s*years?(?:\s+of)?\s+experience",
            r"(?:at least|minimum(?: of)?)\s+(\d+)\s+years?",
            r"(\d+)\s*年(?:以上|及以上).*?经验",
            r"至少\s*(\d+)\s*年.*?经验",
        )
        for value in re.findall(pattern, jd)
    ]
    if experience_matches:
        years = max(experience_matches)
        warnings.append(
            f"JD 明确要求至少 {years} 年相关经验；本模块不会从日期推算或虚构年限，请人工确认是否满足。"
        )

    degree_requirements = (
        ("博士", ("phd", "ph.d", "doctorate", "博士")),
        ("硕士", ("master's", "masters degree", "master degree", "硕士")),
    )
    for label, aliases in degree_requirements:
        if any(_contains(jd, alias) for alias in aliases) and not any(
            _contains(resume, alias) for alias in aliases
        ):
            warnings.append(
                f"JD 提到{label}学历要求，但原简历中未找到对应证据，请人工核对。"
            )
            break

    authorization_terms = (
        "work authorization",
        "authorized to work",
        "visa sponsorship",
        "sponsorship",
        "工作许可",
        "工作签证",
        "签证赞助",
    )
    if any(_contains(jd, term) for term in authorization_terms):
        warnings.append("JD 提到工作许可或签证条件，请在投递前人工确认资格。")
    return warnings


def tailor_resume(
    resume_text: str,
    job_description: str,
    *,
    job_title: str = "",
    company: str = "",
) -> TailoringResult:
    """Create a deterministic, source-faithful resume tailoring draft.

    The function never copies an uncovered JD keyword into ``tailored_text``.
    It only reorders existing sections and bullet payloads, and normalizes bullet
    markers.  Missing skills remain explicit gaps for the user to resolve.
    """

    if not str(resume_text).strip():
        raise TailoringError("简历正文不能为空")
    if not str(job_description).strip():
        raise TailoringError("岗位 JD 不能为空")

    resume_lines = _clean_lines(resume_text)
    keywords = _jd_keywords(job_description)
    parsed = _parse_sections(resume_lines)
    if not parsed:
        raise TailoringError("没有从简历中读取到可用内容")

    tailored_sections: list[tuple[TailoredSection, str, int]] = []
    changes: list[TailoringChange] = []
    for heading, canonical, lines, original_index in parsed:
        ordered_lines, bullet_changes = _reorder_bullet_runs(
            heading, lines, keywords
        )
        changes.extend(bullet_changes)
        body = "\n".join(ordered_lines)
        matches = _line_matches(body, keywords)
        tailored_sections.append(
            (
                TailoredSection(
                    heading=heading,
                    lines=tuple(ordered_lines),
                    relevance_score=len(matches),
                    matched_keywords=matches,
                ),
                canonical,
                original_index,
            )
        )

    # Personal/contact content must remain first. Other complete sections can be
    # reordered safely because no line crosses a section boundary.
    fixed = [item for item in tailored_sections if item[1] == "个人信息"]
    flexible = [item for item in tailored_sections if item[1] != "个人信息"]
    reordered_flexible = sorted(
        flexible,
        key=lambda item: (-item[0].relevance_score, item[2]),
    )
    final_items = fixed + reordered_flexible
    if [item[2] for item in final_items] != [item[2] for item in tailored_sections]:
        changes.append(
            TailoringChange(
                kind="reorder_sections",
                section="简历整体",
                detail="按 JD 关键词相关度前置完整章节，个人信息保持在最前",
            )
        )

    final_sections = tuple(item[0] for item in final_items)
    rendered: list[str] = []
    for section in final_sections:
        if section.heading:
            rendered.append(section.heading)
        rendered.extend(section.lines)
        rendered.append("")
    tailored_text = "\n".join(rendered).strip()

    coverage: list[KeywordCoverage] = []
    for spec in keywords:
        evidence = tuple(
            line for line in resume_lines if _spec_present(line, spec)
        )[:3]
        coverage.append(
            KeywordCoverage(
                keyword=spec.label,
                covered=bool(evidence),
                evidence=evidence,
            )
        )

    missing = [item.keyword for item in coverage if not item.covered]
    gaps = tuple(
        f"JD 提到“{keyword}”，但原简历中未找到证据；只有真实具备时才应补充。"
        for keyword in missing
    )
    warnings = _requirement_warnings(job_description, resume_text)
    if not keywords:
        warnings.append(
            "未从 JD 中识别到可审计的技能关键词；草稿保持原顺序，请人工检查岗位要求。"
        )
    warnings.append(
        "该草稿只使用原简历事实；提交前仍需本人核对姓名、日期、量化结果和岗位资格。"
    )

    return TailoringResult(
        job_title=str(job_title).strip(),
        company=str(company).strip(),
        tailored_text=tailored_text,
        sections=final_sections,
        changes=tuple(changes),
        keyword_coverage=tuple(coverage),
        gaps=gaps,
        warnings=tuple(warnings),
    )


__all__ = [
    "KeywordCoverage",
    "TailoredSection",
    "TailoringChange",
    "TailoringError",
    "TailoringResult",
    "tailor_resume",
]
