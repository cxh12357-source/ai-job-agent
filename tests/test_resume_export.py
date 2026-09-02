from io import BytesIO

import pytest
from docx import Document

from job_assistant.resume_export import ResumeExportError, resume_text_to_docx


def test_exports_same_resume_facts_to_valid_docx():
    content = """陈同学
联系方式
candidate@example.com
技能
- Python
- Excel
工作经历
- 使用 Python 清洗数据"""

    payload = resume_text_to_docx(content)
    document = Document(BytesIO(payload))
    rendered = "\n".join(paragraph.text for paragraph in document.paragraphs)

    assert payload.startswith(b"PK")
    for fact in ("陈同学", "candidate@example.com", "Python", "Excel", "清洗数据"):
        assert fact in rendered


def test_rejects_empty_or_unreasonably_large_draft():
    with pytest.raises(ResumeExportError, match="不能为空"):
        resume_text_to_docx("  ")
    with pytest.raises(ResumeExportError, match="过长"):
        resume_text_to_docx("x" * 200_001)
