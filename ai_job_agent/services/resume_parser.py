from __future__ import annotations

import re
from pathlib import Path


MAX_RESUME_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_RESUME_TEXT_CHARS = 200_000
SUPPORTED_SUFFIXES = frozenset({".pdf", ".docx"})


class ResumeParseError(ValueError):
    pass


def _clean(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    if len(cleaned) < 20:
        raise ResumeParseError("没有读取到足够的简历文字；扫描版 PDF 请先进行 OCR")
    return cleaned


def _pdf_text(content: bytes) -> str:
    if not content.lstrip().startswith(b"%PDF-"):
        raise ResumeParseError("文件内容不是有效的 PDF，请勿只修改扩展名")
    try:
        import pymupdf
    except ImportError as exc:
        raise ResumeParseError("读取 PDF 需要安装 PyMuPDF") from exc
    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            if document.needs_pass:
                raise ResumeParseError("PDF 已加密，请先在本机解密后再上传")
            if len(document) > MAX_PDF_PAGES:
                raise ResumeParseError(f"简历 PDF 不能超过 {MAX_PDF_PAGES} 页")
            chunks: list[str] = []
            total = 0
            for page in document:
                chunk = page.get_text("text")
                total += len(chunk)
                if total > MAX_RESUME_TEXT_CHARS:
                    raise ResumeParseError("简历文字过长，请只上传个人简历")
                chunks.append(chunk)
            return "\n".join(chunks)
    except ResumeParseError:
        raise
    except Exception as exc:
        raise ResumeParseError("PDF 读取失败") from exc


def _docx_text(content: bytes) -> str:
    # Reuse the existing body-order/table/header/text-box aware parser.
    try:
        from job_assistant.resume import extract_resume_text

        return extract_resume_text("resume.docx", content)
    except Exception as exc:
        raise ResumeParseError(str(exc)) from exc


def parse_resume(filename: str, content: bytes) -> str:
    if not content:
        raise ResumeParseError("简历文件为空")
    if len(content) > MAX_RESUME_BYTES:
        raise ResumeParseError("简历文件不能超过 10 MB")
    suffix = Path(filename).suffix.casefold()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ResumeParseError("只支持 PDF 或 DOCX 简历")
    text = _pdf_text(content) if suffix == ".pdf" else _docx_text(content)
    return _clean(text)


def save_upload(
    filename: str, content: bytes, *, uploads_dir: str | Path
) -> Path:
    """Save one validated upload without trusting the client-side path."""

    parse_resume(filename, content)
    suffix = Path(filename).suffix.casefold()
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).stem).strip("._")
    safe_stem = safe_stem[:80] or "resume"
    import hashlib

    digest = hashlib.sha256(content).hexdigest()[:12]
    target_dir = Path(uploads_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{safe_stem}_{digest}{suffix}"
    if not target.exists():
        target.write_bytes(content)
    return target


__all__ = ["ResumeParseError", "parse_resume", "save_upload"]
