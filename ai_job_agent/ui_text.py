"""Treat remote/source text as visible text, never as Markdown resources."""

import re


def markdown_literal(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|<>])", r"\\\1", str(value)).replace("\n", " ").replace("\r", " ")
