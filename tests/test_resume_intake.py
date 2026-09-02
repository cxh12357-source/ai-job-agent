from io import BytesIO
from pathlib import Path

import pymupdf
import pytest
from docx import Document

from ai_job_agent.services import resume_intake
from ai_job_agent.services.resume_intake import process_resume_upload
from ai_job_agent.services.resume_parser import MAX_RESUME_BYTES, ResumeParseError, parse_resume
from ai_job_agent.ui_state import clear_resume_state


def pdf_resume(*, encrypted=False):
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Demo Applicant\ndemo@example.com\nPython Data Analysis project experience")
        if encrypted:
            return document.tobytes(
                encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw="test-owner-password",
                user_pw="test-user-password",
            )
        return document.tobytes()


def test_cloud_upload_parses_pdf_without_writing_original(tmp_path):
    result = process_resume_upload(
        "C:\\fakepath\\resume.pdf", pdf_resume(), cloud_mode=True, uploads_dir=tmp_path
    )
    assert result.filename == "resume.pdf"
    assert result.profile.email == "demo@example.com"
    assert "Python" in result.profile.skills
    assert result.profile.expected_salary is None
    assert result.saved_path == ""
    assert result.size_bytes > 0
    assert list(tmp_path.iterdir()) == []


def test_local_upload_keeps_original_for_user_authorized_filling(tmp_path):
    payload = pdf_resume()
    result = process_resume_upload("resume.pdf", payload, cloud_mode=False, uploads_dir=tmp_path)
    assert Path(result.saved_path).parent == tmp_path
    assert Path(result.saved_path).read_bytes() == payload


def test_docx_upload_is_still_supported_in_cloud(tmp_path):
    document = Document()
    document.add_paragraph("Demo Applicant Python Excel Data Analysis")
    buffer = BytesIO()
    document.save(buffer)
    result = process_resume_upload("resume.docx", buffer.getvalue(), cloud_mode=True, uploads_dir=tmp_path)
    assert result.profile.skills
    assert result.saved_path == ""


def test_ai_processing_is_opt_in(monkeypatch, tmp_path):
    calls = []
    original = resume_intake.build_candidate_profile

    def local_only(text, **kwargs):
        calls.append(kwargs["use_openai"])
        return original(text, use_openai=False)

    monkeypatch.setattr(resume_intake, "build_candidate_profile", local_only)
    process_resume_upload("resume.pdf", pdf_resume(), cloud_mode=True, uploads_dir=tmp_path)
    assert calls == [False]


@pytest.mark.parametrize("payload,message", [
    (b"not a PDF", "不是有效的 PDF"),
    (b"%PDF-broken", "PDF 读取失败"),
    (b"a" * (MAX_RESUME_BYTES + 1), "10 MB"),
], ids=["not-pdf", "corrupt-pdf", "oversized-pdf"])
def test_invalid_upload_is_rejected(payload, message):
    with pytest.raises(ResumeParseError, match=message):
        parse_resume("resume.pdf", payload)


def test_encrypted_pdf_requires_manual_decryption():
    with pytest.raises(ResumeParseError, match="已加密"):
        parse_resume("resume.pdf", pdf_resume(encrypted=True))


def test_scanned_or_empty_pdf_does_not_invent_profile():
    with pymupdf.open() as document:
        document.new_page()
        payload = document.tobytes()
    with pytest.raises(ResumeParseError, match="OCR"):
        parse_resume("scanned.pdf", payload)


def test_clear_session_resume_does_not_touch_other_session():
    first = {"aja_profile": {"name": "Demo Applicant"}, "aja_resume_text": "private", "unrelated": 1}
    second = {"aja_profile": {"name": "Different Demo"}}
    clear_resume_state(first, reset_upload=True)
    assert first["aja_profile"] is None
    assert first["aja_resume_text"] == ""
    assert first["aja_resume_path"] == ""
    assert first["aja_jobs"] == []
    assert first["aja_resume_upload_revision"] == 1
    assert first["aja_saved_profile_checked"] is True
    assert first["unrelated"] == 1
    assert second["aja_profile"]["name"] == "Different Demo"
