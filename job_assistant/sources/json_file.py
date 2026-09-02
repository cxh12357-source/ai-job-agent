from __future__ import annotations

import json
from pathlib import Path

from ..models import Job
from ..salary_parser import parse_salary


def load_jobs_from_json(path: str | Path) -> list[Job]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    records = data.get("jobs", data) if isinstance(data, dict) else data
    jobs: list[Job] = []
    for item in records:
        description = str(item.get("description", ""))
        parsed_salary = parse_salary(description)
        jobs.append(
            Job(
                id=str(item["id"]),
                title=str(item["title"]),
                company=str(item.get("company", "示例公司")),
                location=str(item.get("location", "")),
                description=description,
                url=str(item["url"]),
                source=str(item.get("source", "offline-demo")),
                department=str(item.get("department", "")),
                updated_at=str(item.get("updated_at", "")),
                salary_min=item.get("salary_min", parsed_salary.minimum if parsed_salary else None),
                salary_max=item.get("salary_max", parsed_salary.maximum if parsed_salary else None),
                currency=str(item.get("currency", parsed_salary.currency if parsed_salary else "")),
                period=str(item.get("period", parsed_salary.period if parsed_salary else "")),
                salary_text=str(item.get("salary_text", parsed_salary.text if parsed_salary else "")),
                language=str(item.get("language", "")),
            )
        )
    return jobs
