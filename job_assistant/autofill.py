from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit


REQUIRED_PROFILE_FIELDS = ("first_name", "last_name", "email", "phone")
OPTIONAL_PROFILE_FIELDS = (
    "full_name",
    "location",
    "school",
    "degree",
    "major",
    "graduation_date",
)
MULTILINE_PROFILE_FIELDS = (
    "education",
    "skills",
    "languages",
    "certifications",
    "internships",
    "work_experience",
    "projects",
    "awards",
    "self_introduction",
)
ALLOWED_PROFILE_FIELDS = (
    REQUIRED_PROFILE_FIELDS + OPTIONAL_PROFILE_FIELDS + MULTILINE_PROFILE_FIELDS
)
ALLOWED_RESUME_SUFFIXES = frozenset({".pdf", ".doc", ".docx"})
GREENHOUSE_HOSTS = frozenset(
    {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "boards.eu.greenhouse.io",
        "job-boards.eu.greenhouse.io",
    }
)
SUPPORTED_APPLICATION_SOURCE_PREFIXES = frozenset(
    {
        "greenhouse",
        "lever",
        "ashby",
        "smartrecruiters",
        "li auto campus",
        "officialcareer",
        "official website",
        "official-career",
        "company-career",
        "generic-official",
    }
)
MAX_RESUME_BYTES = 10 * 1024 * 1024
MAX_URL_LENGTH = 4096
PAYLOAD_VERSION = 1

_PROFILE_ALIASES: dict[str, tuple[str, ...]] = {
    "first_name": ("first_name", "given_name", "firstname"),
    "last_name": ("last_name", "family_name", "surname", "lastname"),
    "email": ("email", "email_address"),
    "phone": ("phone", "phone_number", "mobile"),
    "full_name": ("full_name", "name", "legal_name"),
    "location": ("location", "current_location", "city"),
    "school": ("school", "university", "college"),
    "degree": ("degree", "education_level", "highest_degree"),
    "major": ("major", "field_of_study", "specialization"),
    "graduation_date": (
        "graduation_date",
        "graduation_month",
        "expected_graduation_date",
    ),
    "education": ("education", "education_summary", "education_background"),
    "skills": ("skills", "skills_summary", "professional_skills"),
    "languages": ("languages", "language_skills"),
    "certifications": ("certifications", "certification_summary"),
    "internships": ("internships", "internship_summary"),
    "work_experience": ("work_experience", "work_experience_summary"),
    "projects": ("projects", "project_summary"),
    "awards": ("awards", "award_summary"),
    "self_introduction": (
        "self_introduction",
        "personal_summary",
        "professional_summary",
    ),
}
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_ENCODED_CONTROL_PATTERN = re.compile(r"%(?:0[ad]|00)", re.IGNORECASE)


class AutofillError(RuntimeError):
    """Base error for a plan that is unsafe or cannot be executed."""


class UnsafeApplicationUrl(AutofillError):
    pass


class InvalidApplicantProfile(AutofillError):
    pass


class InvalidResume(AutofillError):
    pass


class ConsentRequired(AutofillError):
    pass


def _clean_single_line(value: Any, field_name: str, *, max_length: int = 300) -> str:
    text = str(value or "").strip()
    if not text:
        raise InvalidApplicantProfile(f"{field_name} 不能为空")
    if len(text) > max_length:
        raise InvalidApplicantProfile(f"{field_name} 过长")
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise InvalidApplicantProfile(f"{field_name} 不能包含控制字符")
    return text


def _clean_multiline(value: Any, field_name: str, *, max_length: int = 8_000) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise InvalidApplicantProfile(f"{field_name} 不能为空")
    if len(text) > max_length:
        raise InvalidApplicantProfile(f"{field_name} 过长")
    if any(
        (ord(character) < 32 and character not in {"\n", "\t"})
        or ord(character) == 127
        for character in text
    ):
        raise InvalidApplicantProfile(f"{field_name} 不能包含控制字符")
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _mapping_from_profile(profile: object) -> Mapping[str, Any] | None:
    if isinstance(profile, Mapping):
        return profile
    # ApplicantProfile may expose one of these conventional serialization methods.
    # Only allow-listed keys are read from their result.
    for method_name in ("to_autofill_dict", "to_dict", "model_dump"):
        method = getattr(profile, method_name, None)
        if callable(method):
            result = method()
            if isinstance(result, Mapping):
                return result
    return None


def _read_profile_value(
    profile: object, profile_mapping: Mapping[str, Any] | None, field_name: str
) -> Any:
    for alias in _PROFILE_ALIASES[field_name]:
        if profile_mapping is not None and alias in profile_mapping:
            return profile_mapping[alias]
        if hasattr(profile, alias):
            return getattr(profile, alias)
    return None


def normalize_profile(profile: object) -> tuple[tuple[str, str], ...]:
    """Copy only approved application fields from a profile.

    ``profile`` can be ``job_assistant.profile.ApplicantProfile``, another
    attribute-based object, or a mapping. Only the explicit allow-list is copied;
    identity documents, salary and other sensitive answers are always ignored.
    Core contact fields are required. Known education, experience, projects and
    skills may be supplied as bounded factual summaries for matching text areas.
    """

    if profile is None:
        raise InvalidApplicantProfile("缺少申请人资料")
    profile_mapping = _mapping_from_profile(profile)
    values: list[tuple[str, str]] = []
    for field_name in REQUIRED_PROFILE_FIELDS:
        raw_value = _read_profile_value(profile, profile_mapping, field_name)
        values.append((field_name, _clean_single_line(raw_value, field_name)))

    for field_name in OPTIONAL_PROFILE_FIELDS:
        raw_value = _read_profile_value(profile, profile_mapping, field_name)
        if str(raw_value or "").strip():
            values.append((field_name, _clean_single_line(raw_value, field_name)))

    for field_name in MULTILINE_PROFILE_FIELDS:
        raw_value = _read_profile_value(profile, profile_mapping, field_name)
        if str(raw_value or "").strip():
            values.append((field_name, _clean_multiline(raw_value, field_name)))

    normalized = dict(values)
    if not _EMAIL_PATTERN.fullmatch(normalized["email"]):
        raise InvalidApplicantProfile("email 格式无效")
    return tuple(values)


def _validate_hostname(hostname: str) -> str:
    normalized = hostname.rstrip(".").casefold()
    if not normalized:
        raise UnsafeApplicationUrl("申请地址缺少主机名")
    if normalized == "localhost" or normalized.endswith(
        (".localhost", ".local", ".internal")
    ):
        raise UnsafeApplicationUrl("申请地址不能指向本机或内部网络")
    try:
        ipaddress.ip_address(normalized.strip("[]"))
    except ValueError:
        pass
    else:
        # Greenhouse and employer-hosted application pages use domain names.
        # Rejecting IP literals also avoids obvious private-address navigation.
        raise UnsafeApplicationUrl("申请地址必须使用公网域名")
    try:
        ascii_hostname = normalized.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeApplicationUrl("申请地址的主机名无效") from exc
    if "." not in ascii_hostname or len(ascii_hostname) > 253:
        raise UnsafeApplicationUrl("申请地址必须使用完整公网域名")
    return ascii_hostname


def validate_application_url(value: str) -> str:
    """Validate a public employer or ATS application URL.

    Hosted ATS products and self-built career sites can redirect from an
    employer domain to a separate application domain.  The security boundary is
    therefore HTTPS plus a normal public domain, rather than a single vendor
    allow-list.  This function deliberately rejects local/private-looking hosts,
    credentials and non-standard ports.
    """

    candidate = str(value or "").strip()
    if not candidate or len(candidate) > MAX_URL_LENGTH:
        raise UnsafeApplicationUrl("申请地址为空或过长")
    if any(ord(character) < 32 or ord(character) == 127 for character in candidate):
        raise UnsafeApplicationUrl("申请地址包含控制字符")
    if "\\" in candidate or _ENCODED_CONTROL_PATTERN.search(candidate):
        raise UnsafeApplicationUrl("申请地址格式无效")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeApplicationUrl("申请地址格式无效") from exc
    if parsed.scheme.casefold() != "https":
        raise UnsafeApplicationUrl("官网自动填写仅支持 HTTPS 申请页")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeApplicationUrl("申请地址不能嵌入账号或口令")
    if port not in (None, 443):
        raise UnsafeApplicationUrl("申请地址不允许非标准端口")
    hostname = _validate_hostname(parsed.hostname or "")
    netloc = hostname if port is None else f"{hostname}:443"
    normalized = SplitResult("https", netloc, parsed.path or "/", parsed.query, parsed.fragment)
    return urlunsplit(normalized)


# Backward-compatible public name retained for integrations and older tests.
validate_greenhouse_application_url = validate_application_url


def normalize_application_source(value: str) -> str:
    """Allow only explicitly supported public-job source families."""

    normalized = str(value or "").strip()
    prefix = normalized.partition(":")[0].strip().casefold()
    if prefix not in SUPPORTED_APPLICATION_SOURCE_PREFIXES:
        raise AutofillError(
            "当前自动填写仅支持 Greenhouse、Lever、Ashby、SmartRecruiters "
            "、理想汽车校招或用户指定的企业官方招聘页"
        )
    return normalized


def validate_resume_file(value: str | Path) -> Path:
    candidate = Path(value).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InvalidResume("已选简历文件不存在") from exc
    if not resolved.is_file():
        raise InvalidResume("已选简历不是普通文件")
    if resolved.suffix.casefold() not in ALLOWED_RESUME_SUFFIXES:
        raise InvalidResume("简历仅支持 PDF、DOC 或 DOCX")
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise InvalidResume("无法读取已选简历") from exc
    if not 0 < size <= MAX_RESUME_BYTES:
        raise InvalidResume("简历必须为非空文件且不超过 10 MB")
    return resolved


@dataclass(frozen=True, slots=True)
class AutofillPlan:
    application_url: str
    source: str
    dry_run: bool
    profile_values: tuple[tuple[str, str], ...] = field(repr=False)
    resume_path: Path = field(repr=False)
    consent: bool = field(repr=False)
    allowed_fields: tuple[str, ...] = ALLOWED_PROFILE_FIELDS

    @property
    def fields(self) -> dict[str, str]:
        """Return an in-memory copy; callers must not log this value."""

        return dict(self.profile_values)

    def public_summary(self) -> dict[str, Any]:
        return {
            "application_url": self.application_url,
            "source": self.source,
            "dry_run": self.dry_run,
            "consent_granted": self.consent is True,
            "fields_to_fill": list(self.allowed_fields),
            "resume_selected": True,
            "resume_type": self.resume_path.suffix.casefold(),
            "will_submit": False,
        }

    def require_execution_authorized(self) -> None:
        if self.dry_run:
            raise ConsentRequired("当前是预览计划，不会启动官网填写")
        if self.consent is not True:
            raise ConsentRequired("启动官网填写前必须明确同意")

    def _private_payload(self) -> dict[str, Any]:
        """Payload for the local runner's stdin. Never log or put it in argv."""

        return {
            "version": PAYLOAD_VERSION,
            "application_url": self.application_url,
            "source": self.source,
            "dry_run": self.dry_run,
            "consent": self.consent,
            "profile": self.fields,
            "resume_path": str(self.resume_path),
        }

    def _private_json(self) -> str:
        return json.dumps(self._private_payload(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def _from_private_payload(cls, payload: Mapping[str, Any]) -> "AutofillPlan":
        if payload.get("version") != PAYLOAD_VERSION:
            raise AutofillError("自动填写请求版本不受支持")
        return create_autofill_plan(
            str(payload.get("application_url", "")),
            payload.get("profile"),
            str(payload.get("resume_path", "")),
            source=str(payload.get("source", "")),
            consent=payload.get("consent"),
            dry_run=payload.get("dry_run"),
        )


def create_autofill_plan(
    application_url: str,
    profile: object,
    resume_path: str | Path,
    *,
    consent: bool,
    dry_run: bool = True,
    source: str = "Greenhouse",
) -> AutofillPlan:
    """Build a validated plan; ``consent`` must always be passed explicitly.

    ``dry_run=True`` is the safe default and may be used with ``consent=False``.
    An executable plan requires both ``dry_run=False`` and ``consent=True``.
    """

    if type(consent) is not bool:  # Do not accept truthy strings or integers.
        raise ConsentRequired("consent 必须是明确的布尔值")
    if type(dry_run) is not bool:
        raise AutofillError("dry_run 必须是布尔值")
    if not dry_run and consent is not True:
        raise ConsentRequired("实际启动官网填写前必须明确同意")
    normalized_source = normalize_application_source(source)
    normalized_profile = normalize_profile(profile)
    return AutofillPlan(
        application_url=validate_application_url(application_url),
        source=normalized_source,
        dry_run=dry_run,
        profile_values=normalized_profile,
        resume_path=validate_resume_file(resume_path),
        consent=consent,
        allowed_fields=tuple(field_name for field_name, _ in normalized_profile),
    )


__all__ = [
    "ALLOWED_PROFILE_FIELDS",
    "MULTILINE_PROFILE_FIELDS",
    "OPTIONAL_PROFILE_FIELDS",
    "REQUIRED_PROFILE_FIELDS",
    "SUPPORTED_APPLICATION_SOURCE_PREFIXES",
    "AutofillError",
    "AutofillPlan",
    "ConsentRequired",
    "InvalidApplicantProfile",
    "InvalidResume",
    "UnsafeApplicationUrl",
    "create_autofill_plan",
    "normalize_application_source",
    "normalize_profile",
    "validate_application_url",
    "validate_greenhouse_application_url",
    "validate_resume_file",
]
