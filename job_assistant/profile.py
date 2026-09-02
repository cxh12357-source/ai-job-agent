from __future__ import annotations

import json
import os
import re
import unicodedata
import uuid
import zipfile
from html import unescape
from dataclasses import dataclass, field, replace
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, Mapping

from .resume import MAX_RESUME_BYTES


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE_PATH = PROJECT_ROOT / "data" / "profile.json"
DEFAULT_RESUMES_DIR = PROJECT_ROOT / "data" / "private_resumes"
MAX_PROFILE_BYTES = 1024 * 1024

ResumeLanguage = Literal["zh", "en"]


class ProfileError(ValueError):
    """本地申请资料或简历配置无效。"""


@dataclass(frozen=True, slots=True)
class ApplicantProfile:
    """仅保存表单资料和简历文件引用，不保存简历正文。"""

    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    chinese_resume: str = ""
    english_resume: str = ""
    application_fields: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ApplicantProfile":
        if not isinstance(data, Mapping):
            raise ProfileError("本地申请资料必须是 JSON 对象")

        resumes = data.get("resumes", {})
        if resumes is None:
            resumes = {}
        if not isinstance(resumes, Mapping):
            raise ProfileError("resumes 必须是 JSON 对象")

        fields = data.get("application_fields", {})
        if fields is None:
            fields = {}
        if not isinstance(fields, Mapping):
            raise ProfileError("application_fields 必须是 JSON 对象")

        first_name, last_name = _profile_names(data)
        return cls(
            first_name=first_name,
            last_name=last_name,
            email=_string_field(data, "email"),
            phone=_string_field(data, "phone"),
            location=_string_field(data, "location"),
            chinese_resume=_optional_string(
                resumes.get("zh") or data.get("chinese_resume"),
                "chinese_resume",
            ),
            english_resume=_optional_string(
                resumes.get("en") or data.get("english_resume"),
                "english_resume",
            ),
            application_fields={
                _optional_string(key, "application_fields 键"): _optional_string(
                    value, f"application_fields.{key}"
                )
                for key, value in fields.items()
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "email": self.email,
            "phone": self.phone,
            "location": self.location,
            "resumes": {
                "zh": self.chinese_resume,
                "en": self.english_resume,
            },
            "application_fields": dict(self.application_fields),
        }

    @property
    def full_name(self) -> str:
        """为展示和旧调用方提供组合姓名，自动填表仍应使用独立字段。"""

        return " ".join(part for part in (self.first_name, self.last_name) if part)

    def with_resume(
        self, language: str, stored_path: str | Path
    ) -> "ApplicantProfile":
        """返回绑定了已存储简历的新资料对象。"""

        normalized = normalize_resume_language(language)
        reference = Path(stored_path).name
        if not reference:
            raise ProfileError("简历文件引用不能为空")
        if normalized == "en":
            return replace(self, english_resume=reference)
        return replace(self, chinese_resume=reference)


def load_profile(path: str | Path = DEFAULT_PROFILE_PATH) -> ApplicantProfile:
    profile_path = Path(path)
    if not profile_path.exists():
        return ApplicantProfile()
    try:
        if profile_path.stat().st_size > MAX_PROFILE_BYTES:
            raise ProfileError("本地申请资料文件不能超过 1 MB")
        data = json.loads(profile_path.read_text(encoding="utf-8"))
    except ProfileError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProfileError("本地申请资料读取失败，请检查 profile.json") from exc
    return ApplicantProfile.from_dict(data)


def save_profile(
    profile: ApplicantProfile, path: str | Path = DEFAULT_PROFILE_PATH
) -> Path:
    """原子写入本地 JSON，避免中途中断损坏原配置。"""

    if not isinstance(profile, ApplicantProfile):
        raise ProfileError("profile 必须是 ApplicantProfile")
    profile_path = Path(path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        profile.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
    if len(payload.encode("utf-8")) > MAX_PROFILE_BYTES:
        raise ProfileError("本地申请资料文件不能超过 1 MB")

    temporary = profile_path.with_name(
        f".{profile_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        _write_private_new_file(temporary, payload.encode("utf-8"))
        os.replace(temporary, profile_path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ProfileError("本地申请资料保存失败") from exc
    return profile_path


def store_resume(
    filename: str,
    content: bytes,
    language: str,
    *,
    resumes_dir: str | Path = DEFAULT_RESUMES_DIR,
) -> Path:
    """
    把简历存入私有目录。

    不使用上传文件的路径，且每次生成唯一文件名，因此不会覆盖
    已有简历。本函数不解析、返回或记录简历正文。
    """

    normalized = normalize_resume_language(language)
    if not isinstance(content, bytes):
        if isinstance(content, (bytearray, memoryview)):
            content = bytes(content)
        else:
            raise ProfileError("简历内容必须是二进制数据")
    if not content:
        raise ProfileError("简历文件是空的")
    if len(content) > MAX_RESUME_BYTES:
        raise ProfileError("简历文件不能超过 10 MB")

    original_name = _basename(filename)
    suffix = Path(original_name).suffix.casefold()
    if suffix not in {".pdf", ".docx"}:
        raise ProfileError("申请用简历仅支持 PDF 或 DOCX")
    _validate_resume_content(content, suffix)

    stem = _safe_stem(Path(original_name).stem)
    destination_dir = Path(resumes_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination_dir = destination_dir.resolve()

    for _ in range(10):
        unique_name = f"{stem}_{normalized}_{uuid.uuid4().hex[:12]}{suffix}"
        destination = destination_dir / unique_name
        try:
            _write_private_new_file(destination, content)
            return destination
        except FileExistsError:
            continue
        except OSError as exc:
            raise ProfileError("简历文件保存失败") from exc
    raise ProfileError("无法生成唯一的简历文件名，请重试")


def attach_resume(
    profile: ApplicantProfile,
    filename: str,
    content: bytes,
    language: str,
    *,
    resumes_dir: str | Path = DEFAULT_RESUMES_DIR,
) -> tuple[ApplicantProfile, Path]:
    """存储简历并返回已更新引用的资料对象；由调用方确认后保存 JSON。"""

    stored_path = store_resume(
        filename, content, language, resumes_dir=resumes_dir
    )
    return profile.with_resume(language, stored_path), stored_path


def choose_resume_language(
    job_language: str = "", company_foreign: bool = False
) -> ResumeLanguage:
    """岗位语言有明确值时优先；不明确时外企默认英文。"""

    language = str(job_language or "").strip().casefold()
    if language:
        english_markers = ("english", "英文", "英语", "英語")
        chinese_markers = (
            "chinese",
            "mandarin",
            "中文",
            "汉语",
            "漢語",
            "普通话",
            "國語",
        )
        if re.search(r"(?:^|[^a-z])en(?:[-_][a-z]{2})?(?:$|[^a-z])", language):
            return "en"
        if any(marker in language for marker in english_markers):
            return "en"
        if re.search(r"(?:^|[^a-z])zh(?:[-_][a-z]{2})?(?:$|[^a-z])", language):
            return "zh"
        if any(marker in language for marker in chinese_markers):
            return "zh"
    return "en" if company_foreign else "zh"


def infer_company_foreign(
    company: str,
    job_language: str = "",
    title: str = "",
    description: str = "",
) -> bool:
    """推断是否应把岗位视为英文投递场景。

    这是一个供 UI 预选的保守信号，不是对企业国籍的事实判定；调用方
    应允许用户覆盖。岗位明确声明英文时返回 True，明确声明中文
    时返回 False。未声明语言时，只在标题和 JD 有足够英文且中文很少
    时返回 True。

    ``company`` 特意不参与规则，避免依赖不可靠的公司国籍名单或
    从公司名称猜测。
    """

    # 保留参数供调用方传递完整岗位上下文，但不用公司名做国籍推断。
    _ = company
    language = str(job_language or "").strip().casefold()
    english_declared = _contains_english_language_marker(language)
    chinese_declared = _contains_chinese_language_marker(language)

    # 双语标注不是“明确英文”，保守地不自动判为外企场景。
    if chinese_declared:
        return False
    if english_declared:
        return True

    visible_text = _visible_language_text(f"{title or ''}\n{description or ''}")
    latin_letters = len(re.findall(r"[A-Za-z]", visible_text))
    chinese_characters = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", visible_text))
    english_words = len(re.findall(r"\b[A-Za-z]{2,}\b", visible_text))
    language_letters = latin_letters + chinese_characters

    if latin_letters < 40 or english_words < 6 or language_letters == 0:
        return False
    chinese_share = chinese_characters / language_letters
    return chinese_characters <= 8 and chinese_share <= 0.05


def select_resume(
    profile: ApplicantProfile,
    job_language: str = "",
    company_foreign: bool = False,
    *,
    resumes_dir: str | Path = DEFAULT_RESUMES_DIR,
) -> Path:
    """返回该岗位应使用的本地简历，不会打开或记录文件内容。"""

    if not isinstance(profile, ApplicantProfile):
        raise ProfileError("profile 必须是 ApplicantProfile")
    language = choose_resume_language(job_language, company_foreign)
    label = "英文" if language == "en" else "中文"
    reference = (
        profile.english_resume if language == "en" else profile.chinese_resume
    )
    if not reference:
        raise ProfileError(
            f"该岗位需要{label}简历，但本地资料中尚未配置{label}版简历"
        )

    root = Path(resumes_dir).resolve()
    configured = Path(reference)
    candidate = configured if configured.is_absolute() else root / configured
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ProfileError(f"已配置的{label}简历路径不安全，请重新上传") from exc

    if not resolved.is_file():
        raise ProfileError(f"已配置的{label}简历文件不存在，请重新上传")
    if resolved.suffix.casefold() not in {".pdf", ".docx"}:
        raise ProfileError(f"已配置的{label}简历类型不受支持，请重新上传")
    return resolved


def normalize_resume_language(language: str) -> ResumeLanguage:
    normalized = str(language or "").strip().casefold().replace("_", "-")
    aliases: dict[str, ResumeLanguage] = {
        "zh": "zh",
        "zh-cn": "zh",
        "cn": "zh",
        "chinese": "zh",
        "中文": "zh",
        "中文版": "zh",
        "en": "en",
        "en-us": "en",
        "en-gb": "en",
        "english": "en",
        "英文": "en",
        "英文版": "en",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ProfileError("简历语言必须是中文（zh）或英文（en）") from exc


def _contains_english_language_marker(language: str) -> bool:
    if not language:
        return False
    if re.search(r"(?:^|[^a-z])en(?:[-_][a-z]{2})?(?:$|[^a-z])", language):
        return True
    return any(marker in language for marker in ("english", "英文", "英语", "英語"))


def _contains_chinese_language_marker(language: str) -> bool:
    if not language:
        return False
    if re.search(r"(?:^|[^a-z])zh(?:[-_][a-z]{2})?(?:$|[^a-z])", language):
        return True
    return any(
        marker in language
        for marker in (
            "chinese",
            "mandarin",
            "中文",
            "汉语",
            "漢語",
            "普通话",
            "國語",
        )
    )


def _visible_language_text(value: str) -> str:
    """去掉 HTML 标记、URL 和邮箱，避免将技术字符误当成 JD 语言。"""

    text = re.sub(
        r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        " ",
        str(value or ""),
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"https?://\S+|www\.\S+", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\b[^\s@]+@[^\s@]+\b", " ", text)


def _string_field(data: Mapping[str, Any], name: str) -> str:
    return _optional_string(data.get(name), name)


def _profile_names(data: Mapping[str, Any]) -> tuple[str, str]:
    first_name = _string_field(data, "first_name")
    last_name = _string_field(data, "last_name")
    if first_name or last_name:
        return first_name, last_name

    # 兼容本模块早期的单字段草案；不猜测中文姓名的姓与名。
    legacy_full_name = _string_field(data, "full_name")
    return legacy_full_name, ""


def _optional_string(value: Any, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ProfileError(f"{name} 必须是文本")
    return value.strip()


def _basename(filename: str) -> str:
    if not isinstance(filename, str) or not filename.strip():
        raise ProfileError("简历文件名不能为空")
    return filename.replace("\\", "/").rsplit("/", 1)[-1]


def _safe_stem(stem: str) -> str:
    normalized = unicodedata.normalize("NFKC", stem)
    normalized = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", normalized)
    normalized = re.sub(r"[^\w\-. ]+", "_", normalized, flags=re.UNICODE)
    normalized = re.sub(r"[\s._-]+", "_", normalized).strip("_. ")
    normalized = normalized[:60].rstrip("_. ") or "resume"
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update(f"COM{number}" for number in range(1, 10))
    reserved.update(f"LPT{number}" for number in range(1, 10))
    return "resume" if normalized.upper() in reserved else normalized


def _validate_resume_content(content: bytes, suffix: str) -> None:
    if suffix == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise ProfileError("PDF 简历内容与文件类型不符")
        return

    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ProfileError("DOCX 简历内容与文件类型不符")
    except (zipfile.BadZipFile, OSError) as exc:
        raise ProfileError("DOCX 简历内容与文件类型不符") from exc


def _write_private_new_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
