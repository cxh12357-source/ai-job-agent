import csv
import io

from job_assistant.exporting import results_to_csv
from job_assistant.models import Job, MatchResult


def test_csv_export_neutralizes_spreadsheet_formulas():
    job = Job(
        id="1",
        title="=HYPERLINK(\"https://evil.example\")",
        company="+SUM(1,1)",
        location="上海",
        description="Python",
        url="https://example.com/1",
        source="test",
    )
    csv_text = results_to_csv([MatchResult(job=job, score=80, eligible=True)])
    row = next(csv.DictReader(io.StringIO(csv_text)))

    assert row["title"].startswith("'=")
    assert row["company"].startswith("'+")
    assert row["url"] == "https://example.com/1"
