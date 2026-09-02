from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from job_assistant.profile import (
    ApplicantProfile,
    ProfileError,
    attach_resume,
    choose_resume_language,
    infer_company_foreign,
    load_profile,
    save_profile,
    select_resume,
    store_resume,
)
from job_assistant.resume import MAX_RESUME_BYTES


def _pdf(body: bytes = b"safe test content") -> bytes:
    return b"%PDF-1.7\n" + body + b"\n%%EOF"


def _docx() -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
    return stream.getvalue()


def test_profile_round_trip_uses_local_json_without_resume_body(tmp_path: Path):
    profile_path = tmp_path / "data" / "profile.json"
    profile = ApplicantProfile(
        first_name="Xiaohua",
        last_name="Chen",
        email="test@example.com",
        phone="13800000000",
        location="Shanghai",
        chinese_resume="resume_zh_123.pdf",
        english_resume="resume_en_456.docx",
        application_fields={"linkedin": "https://example.com/profile"},
    )

    assert save_profile(profile, profile_path) == profile_path
    assert load_profile(profile_path) == profile
    raw = profile_path.read_text(encoding="utf-8")
    assert '"schema_version": 1' in raw
    assert '"first_name": "Xiaohua"' in raw
    assert profile.full_name == "Xiaohua Chen"
    assert '"resumes"' in raw
    assert "resume_text" not in raw


def test_missing_profile_returns_empty_profile(tmp_path: Path):
    assert load_profile(tmp_path / "missing.json") == ApplicantProfile()


def test_store_resume_sanitizes_name_and_never_overwrites(tmp_path: Path):
    resumes_dir = tmp_path / "private_resumes"
    first = store_resume(
        r"..\..\CON?.pdf", _pdf(b"first version"), "zh", resumes_dir=resumes_dir
    )
    second = store_resume(
        r"..\..\CON?.pdf", _pdf(b"second version"), "zh", resumes_dir=resumes_dir
    )

    assert first.parent == resumes_dir.resolve()
    assert second.parent == resumes_dir.resolve()
    assert first != second
    assert first.read_bytes() == _pdf(b"first version")
    assert second.read_bytes() == _pdf(b"second version")
    assert ".." not in first.name
    assert "\\" not in first.name


def test_store_resume_accepts_real_docx_container(tmp_path: Path):
    stored = store_resume(
        "English Resume.docx", _docx(), "English", resumes_dir=tmp_path
    )
    assert stored.suffix == ".docx"
    assert "_en_" in stored.name


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("resume.txt", b"plain text", "PDF 或 DOCX"),
        ("resume.pdf", b"not a pdf", "类型不符"),
        ("resume.docx", b"not a zip", "类型不符"),
        ("resume.pdf", b"", "空的"),
    ],
)
def test_store_resume_rejects_invalid_files(
    tmp_path: Path, filename: str, content: bytes, message: str
):
    with pytest.raises(ProfileError, match=message):
        store_resume(filename, content, "zh", resumes_dir=tmp_path)


def test_store_resume_enforces_size_limit(tmp_path: Path):
    content = b"%PDF-" + b"x" * MAX_RESUME_BYTES
    with pytest.raises(ProfileError, match="10 MB"):
        store_resume("large.pdf", content, "zh", resumes_dir=tmp_path)


def test_attach_resume_updates_only_requested_language(tmp_path: Path):
    initial = ApplicantProfile(chinese_resume="existing.pdf")
    updated, stored = attach_resume(
        initial,
        "Resume.docx",
        _docx(),
        "en",
        resumes_dir=tmp_path,
    )
    assert updated.chinese_resume == "existing.pdf"
    assert updated.english_resume == stored.name
    assert initial.english_resume == ""


@pytest.mark.parametrize(
    ("job_language", "company_foreign", "expected"),
    [
        ("English", False, "en"),
        ("en-US", False, "en"),
        ("英语必须", False, "en"),
        ("Chinese", True, "zh"),
        ("zh-CN", True, "zh"),
        ("中文岗位", True, "zh"),
        ("", True, "en"),
        ("", False, "zh"),
        ("not specified", True, "en"),
    ],
)
def test_choose_resume_language(
    job_language: str, company_foreign: bool, expected: str
):
    assert choose_resume_language(job_language, company_foreign) == expected


@pytest.mark.parametrize("job_language", ["English", "en", "en-US", "英文", "英语必须"])
def test_infer_company_foreign_honors_explicit_english(job_language: str):
    assert infer_company_foreign("任意公司", job_language=job_language) is True


@pytest.mark.parametrize("job_language", ["Chinese", "zh", "zh-CN", "中文", "普通话"])
def test_infer_company_foreign_honors_explicit_chinese(job_language: str):
    english_jd = "Build scalable products with a global engineering team in English."
    assert (
        infer_company_foreign(
            "Any Company", job_language=job_language, description=english_jd
        )
        is False
    )


def test_infer_company_foreign_uses_english_dominant_title_and_jd():
    assert (
        infer_company_foreign(
            "Example Company",
            title="Senior Software Engineer",
            description=(
                "Design reliable distributed systems and collaborate with product, "
                "security, and platform engineering teams across global regions. 上海"
            ),
        )
        is True
    )


def test_infer_company_foreign_is_conservative_for_chinese_or_mixed_jd():
    assert (
        infer_company_foreign(
            "Example Company",
            title="Software Engineer 软件工程师",
            description=(
                "负责分布式系统的设计开发与维护，与产品和安全团队密切合作，"
                "using Python, SQL, cloud services, and modern engineering practices."
            ),
        )
        is False
    )
    assert infer_company_foreign("Example Company", title="Software Engineer") is False


def test_infer_company_foreign_does_not_guess_from_company_name():
    assert infer_company_foreign("Global Foreign Corporation") is False
    assert infer_company_foreign("知名跨国企业") is False


def test_infer_company_foreign_treats_bilingual_declaration_conservatively():
    assert infer_company_foreign("Any", job_language="English / 中文") is False


def test_select_resume_routes_by_job_language_before_company_type(tmp_path: Path):
    zh = tmp_path / "resume_zh.pdf"
    en = tmp_path / "resume_en.docx"
    zh.write_bytes(_pdf())
    en.write_bytes(_docx())
    profile = ApplicantProfile(chinese_resume=zh.name, english_resume=en.name)

    assert select_resume(
        profile, "Chinese", company_foreign=True, resumes_dir=tmp_path
    ) == zh.resolve()
    assert select_resume(
        profile, "English", company_foreign=False, resumes_dir=tmp_path
    ) == en.resolve()
    assert select_resume(
        profile, company_foreign=True, resumes_dir=tmp_path
    ) == en.resolve()


def test_select_resume_reports_missing_version_clearly(tmp_path: Path):
    with pytest.raises(ProfileError, match="尚未配置英文版简历"):
        select_resume(
            ApplicantProfile(), company_foreign=True, resumes_dir=tmp_path
        )

    profile = ApplicantProfile(chinese_resume="missing.pdf")
    with pytest.raises(ProfileError, match="中文简历文件不存在"):
        select_resume(profile, resumes_dir=tmp_path)


def test_select_resume_rejects_reference_outside_private_directory(tmp_path: Path):
    private_dir = tmp_path / "private"
    private_dir.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(_pdf())
    profile = ApplicantProfile(chinese_resume=str(outside))

    with pytest.raises(ProfileError, match="路径不安全"):
        select_resume(profile, resumes_dir=private_dir)


def test_resume_body_is_never_written_to_logs(tmp_path: Path, caplog):
    secret = b"%PDF-1.7\nSECRET_RESUME_BODY_918273\n%%EOF"
    stored = store_resume("resume.pdf", secret, "zh", resumes_dir=tmp_path)
    profile = ApplicantProfile(chinese_resume=stored.name)
    assert select_resume(profile, resumes_dir=tmp_path) == stored.resolve()
    assert "SECRET_RESUME_BODY_918273" not in caplog.text
