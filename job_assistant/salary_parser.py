from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParsedSalary:
    minimum: float
    maximum: float
    currency: str
    period: str
    text: str


RANGE_PATTERN = re.compile(
    r"(?P<prefix>USD|US\$|CNY|RMB|EUR|GBP|[$￥¥€£])?\s*"
    r"(?P<minimum>\d{1,3}(?:[,，]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<min_suffix>[kK千萬万]?)\s*"
    r"(?:-|–|—|~|～|至|to)\s*"
    r"(?P<second_prefix>USD|US\$|CNY|RMB|EUR|GBP|[$￥¥€£])?\s*"
    r"(?P<maximum>\d{1,3}(?:[,，]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<max_suffix>[kK千萬万]?)",
    re.IGNORECASE,
)


def _number(value: str, suffix: str) -> float:
    number = float(value.replace(",", "").replace("，", ""))
    normalized_suffix = unicodedata.normalize("NFKC", suffix).casefold()
    if normalized_suffix == "k" or normalized_suffix == "千":
        return number * 1_000
    if normalized_suffix == "万":
        return number * 10_000
    return number


def _currency(prefix: str, context: str) -> str:
    token = unicodedata.normalize("NFKC", prefix).upper()
    if token in {"$", "US$", "USD"}:
        return "USD"
    if token in {"￥", "¥", "CNY", "RMB"}:
        return "CNY"
    if token in {"€", "EUR"}:
        return "EUR"
    if token in {"£", "GBP"}:
        return "GBP"
    lowered = context.casefold()
    if re.search(r"\b(?:usd|us dollars?)\b", lowered):
        return "USD"
    if re.search(r"\b(?:cny|rmb)\b|人民币|元|薪资|薪酬|工资|月薪|年薪", lowered):
        return "CNY"
    if re.search(r"\beur\b|欧元", lowered):
        return "EUR"
    if re.search(r"\bgbp\b|英镑", lowered):
        return "GBP"
    return ""


def _period(context: str) -> str:
    lowered = context.casefold()
    if re.search(r"/\s*(?:月|mo(?:nth)?)\b|每月|月薪|per month|monthly", lowered):
        return "month"
    if re.search(r"/\s*(?:年|yr|year)\b|每年|年薪|per year|annual|annum|yearly", lowered):
        return "year"
    return ""


def parse_salary(text: str) -> ParsedSalary | None:
    """从 JD 中提取一个明确的薪资范围；不猜测单个金额或单位。

    只接受含上下限的范围，避免把奖金、学习预算等单一金额误当薪资。
    未识别币种或周期时仍保留数值，但相应字段为空，由筛选策略决定是否人工复核。
    """

    normalized = unicodedata.normalize("NFKC", str(text))
    for match in RANGE_PATTERN.finditer(normalized):
        trailing = normalized[match.end() : match.end() + 24]
        # 排除“每周 4-5 天”“3-6 个月”“3-5 年经验”等时间范围；
        # Greenhouse 的 HTML 转纯文本后换行可能消失，不能只依赖整句语境。
        if re.match(
            r"\s*(?:天|日|周|星期|个月|月(?:以上)?经验|年(?:以上)?经验|"
            r"days?\b|weeks?\b|months?\b|years?\b|hours?\b)",
            trailing,
            re.IGNORECASE,
        ):
            continue
        # 只在同一句/分句里寻找薪资标记，避免后文的学习预算、年度福利等
        # 把前面的“3-5 years experience”误识别为年薪范围。
        previous_boundary = max(
            normalized.rfind(marker, 0, match.start())
            for marker in ".!?;。！？；\n"
        )
        next_boundaries = [
            position
            for marker in ".!?;。！？；\n"
            if (position := normalized.find(marker, match.end())) != -1
        ]
        start = previous_boundary + 1
        end = min(next_boundaries) if next_boundaries else len(normalized)
        context = normalized[start:end]
        local_context = normalized[max(start, match.start() - 80) : min(end, match.end() + 80)]
        prefix = match.group("prefix") or match.group("second_prefix") or ""
        currency = _currency(prefix, local_context)
        period = _period(local_context)
        minimum = _number(match.group("minimum"), match.group("min_suffix"))
        maximum = _number(match.group("maximum"), match.group("max_suffix"))

        # 例如 20-30k 通常只在上限写单位，向下限传播同一个明确后缀。
        if not match.group("min_suffix") and match.group("max_suffix"):
            minimum = _number(match.group("minimum"), match.group("max_suffix"))
        if minimum <= 0 or maximum <= 0 or minimum > maximum:
            continue
        # 没有任何薪资语境时不采信普通数字范围（如“3-5 年经验”）。
        salary_context = bool(
            currency
            or period
            or re.search(
                r"薪资|薪酬|工资|base pay|salary|compensation|pay range",
                local_context,
                re.I,
            )
        )
        if not salary_context:
            continue
        return ParsedSalary(
            minimum=minimum,
            maximum=maximum,
            currency=currency,
            period=period,
            text=match.group(0).strip(),
        )
    return None
