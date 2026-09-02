from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CandidateSignals:
    keywords: tuple[str, ...]
    suggested_titles: tuple[str, ...]


SIGNAL_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Python", ("python",)),
    ("Excel", ("excel",)),
    ("SQL", ("sql",)),
    ("Power BI", ("power bi", "powerbi")),
    ("Data Analysis", ("data analysis", "data analytics", "数据分析", "数据清洗", "统计分析")),
    ("Data Visualization", ("data visualization", "数据可视化", "可视化")),
    ("AI Agent", ("ai agent", "agentic ai", "智能体")),
    ("LLM", ("llm", "large language model", "大模型")),
    ("Prompt Engineering", ("prompt engineering", "提示词", "提示工程")),
    ("Automation", ("automation", "workflow", "自动化", "工作流")),
    ("API", ("api", "接口")),
    ("Git", ("git", "github", "gitlab")),
    ("Linux", ("linux", "unix")),
    ("Thermal Management", ("thermal management", "热管理")),
    ("Heat Transfer", ("heat transfer", "传热")),
    ("Fluid Mechanics", ("fluid mechanics", "流体力学")),
    ("Battery", ("battery", "电池", "电芯")),
    ("Manufacturing", ("manufacturing", "智能制造", "制造工程", "生产工艺")),
    ("Mechanical Design", ("mechanical design", "机械设计")),
    ("SolidWorks", ("solidworks", "solid works")),
    ("AutoCAD", ("autocad", "auto cad")),
    ("Testing", ("testing", "test engineer", "测试", "试验")),
    ("Sales", ("sales", "销售", "客户开发")),
    ("Customer Communication", ("customer communication", "client communication", "客户沟通", "客户需求")),
    ("English", ("english", "英语", "英文")),
)


TITLE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("热管理工程师", ("Thermal Management", "Heat Transfer", "Fluid Mechanics")),
    ("机械工程师", ("Mechanical Design", "SolidWorks", "AutoCAD", "Thermal Management")),
    ("制造工程师", ("Manufacturing", "SolidWorks", "AutoCAD")),
    ("工艺工程师", ("Manufacturing", "Testing", "Battery")),
    ("测试工程师", ("Testing", "Thermal Management", "Battery")),
    ("数据分析实习生", ("Data Analysis", "Python", "Excel", "Power BI", "SQL")),
    ("AI应用", ("AI Agent", "LLM", "Prompt Engineering")),
    ("自动化工程师", ("Automation", "Python", "API")),
    ("Technical Solution Engineer", ("Customer Communication", "API", "AI Agent")),
    ("Account Operations Intern", ("Sales", "Customer Communication", "Data Analysis")),
)


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def _contains(text: str, term: str) -> bool:
    normalized_text = _norm(text)
    normalized_term = _norm(term)
    if re.fullmatch(r"[a-z0-9+#.]+(?: [a-z0-9+#.]+)*", normalized_term):
        return re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])",
            normalized_text,
        ) is not None
    return normalized_term in normalized_text


def extract_candidate_signals(resume_text: str, *, keyword_limit: int = 16) -> CandidateSignals:
    """Extract conservative search signals that are explicitly present in a resume."""

    text = str(resume_text or "").strip()
    if not text:
        return CandidateSignals((), ())
    keywords = tuple(
        label
        for label, aliases in SIGNAL_ALIASES
        if any(_contains(text, alias) for alias in aliases)
    )[:keyword_limit]
    present = set(keywords)
    titles = tuple(
        title
        for title, triggers in TITLE_RULES
        if any(trigger in present for trigger in triggers)
    )
    return CandidateSignals(keywords=keywords, suggested_titles=titles)


__all__ = ["CandidateSignals", "extract_candidate_signals"]
