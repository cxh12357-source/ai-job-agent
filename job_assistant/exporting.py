from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .models import MatchResult


def _spreadsheet_safe(value: object) -> object:
    if not isinstance(value, str):
        return value
    stripped = value.lstrip()
    if stripped.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _safe_row(result: MatchResult) -> dict[str, object]:
    return {key: _spreadsheet_safe(value) for key, value in result.to_dict().items()}


def results_to_csv(results: list[MatchResult]) -> str:
    stream = io.StringIO(newline="")
    fieldnames = list(results[0].to_dict()) if results else ["job_id", "title", "score"]
    writer = csv.DictWriter(stream, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(_safe_row(result) for result in results)
    return stream.getvalue()


def write_results(results: list[MatchResult], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        path.write_text(
            json.dumps([result.to_dict() for result in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        path.write_text("\ufeff" + results_to_csv(results), encoding="utf-8")
    return path
