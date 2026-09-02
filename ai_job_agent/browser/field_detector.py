from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .models import FieldMatch, FormControl, coerce_controls


PUBLIC_AUTOFILL_FIELDS = frozenset(
    {
        "first_name",
        "last_name",
        "full_name",
        "email",
        "phone",
        "location",
        "resume",
        "linkedin",
        "portfolio",
        "school",
        "degree",
        "major",
        "graduation_date",
        "education",
        "work_experience",
        "internships",
        "projects",
    }
)
ANSWER_GENERATOR_FIELDS = frozenset({"self_introduction", "open_question"})
FILLABLE_FIELDS = PUBLIC_AUTOFILL_FIELDS | ANSWER_GENERATOR_FIELDS
SENSITIVE_FIELDS = frozenset(
    {
        "work_authorization",
        "visa_sponsorship",
        "salary_expectation",
        "date_of_birth",
        "national_id",
        "gender",
        "race_ethnicity",
        "disability",
        "veteran_status",
        "criminal_history",
    }
)


def _compact(value: str) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").casefold(), flags=re.UNICODE)


FIELD_PATTERNS: dict[str, tuple[str, ...]] = {
    "first_name": ("firstname", "givenname", "名", "名字"),
    "last_name": ("lastname", "familyname", "surname", "姓", "姓氏"),
    "full_name": ("fullname", "legalname", "姓名", "yourname"),
    "email": ("email", "emailaddress", "电子邮箱", "邮箱", "邮件地址"),
    "phone": ("phone", "phonenumber", "mobile", "telephone", "手机号", "电话"),
    "location": (
        "currentlocation",
        "location",
        "currentcity",
        "city",
        "所在地",
        "现居地",
        "城市",
    ),
    "resume": ("resume", "résumé", "curriculumvitae", "cv", "简历", "附件简历"),
    "linkedin": ("linkedin", "linkedinprofile"),
    "portfolio": ("portfolio", "personalwebsite", "website", "作品集", "个人网站"),
    "school": (
        "school",
        "university",
        "college",
        "educationalinstitution",
        "学校",
        "院校",
        "大学",
    ),
    "degree": ("degree", "qualification", "学历", "学位"),
    "major": ("major", "fieldofstudy", "discipline", "专业", "所学专业"),
    "graduation_date": (
        "graduationdate",
        "graduationyear",
        "expectedgraduation",
        "毕业时间",
        "毕业日期",
        "预计毕业",
    ),
    "education": (
        "educationhistory",
        "educationbackground",
        "academicbackground",
        "教育经历",
        "教育背景",
    ),
    "work_experience": (
        "workexperience",
        "employmenthistory",
        "professionalexperience",
        "工作经历",
        "职业经历",
    ),
    "internships": ("internshipexperience", "internships", "实习经历", "实习经验"),
    "projects": ("projectexperience", "projects", "项目经历", "项目经验"),
    "self_introduction": (
        "selfintroduction",
        "personalstatement",
        "aboutyourself",
        "introduceyourself",
        "自我介绍",
        "个人陈述",
    ),
    "open_question": (
        "openquestion",
        "additionalinformation",
        "whydoyouwanttojoin",
        "whydoyouwanttowork",
        "motivation",
        "补充信息",
        "为什么加入",
        "求职动机",
    ),
    "work_authorization": (
        "workauthorization",
        "authorizedtowork",
        "righttowork",
        "workpermit",
        "工作许可",
        "工作授权",
    ),
    "visa_sponsorship": (
        "visasponsorship",
        "requiresponsorship",
        "immigrationsponsorship",
        "签证担保",
        "签证支持",
    ),
    "salary_expectation": (
        "expectedsalary",
        "salaryexpectation",
        "desiredsalary",
        "期望薪资",
        "薪资要求",
    ),
    "date_of_birth": ("dateofbirth", "birthdate", "birthday", "出生日期", "生日"),
    "national_id": (
        "nationalid",
        "identitynumber",
        "idnumber",
        "socialsecuritynumber",
        "ssn",
        "身份证",
        "证件号码",
    ),
    "gender": ("gender", "sex", "性别"),
    "race_ethnicity": ("race", "ethnicity", "racial", "种族", "族裔", "民族"),
    "disability": ("disability", "disabled", "残障", "残疾"),
    "veteran_status": ("veteran", "militaryservice", "退伍军人", "服役"),
    "criminal_history": ("criminalhistory", "conviction", "犯罪记录", "刑事记录"),
}

AUTOCOMPLETE_FIELDS = {
    "given-name": "first_name",
    "family-name": "last_name",
    "name": "full_name",
    "email": "email",
    "tel": "phone",
    "address-level1": "location",
    "address-level2": "location",
    "country-name": "location",
    "url": "portfolio",
    "bday": "date_of_birth",
}

SOURCE_WEIGHTS = {
    "autocomplete": 0.99,
    "label": 0.95,
    "aria_label": 0.92,
    "aria_description": 0.86,
    "placeholder": 0.86,
    "name": 0.84,
    "id": 0.82,
    "nearby_text": 0.64,
    "input_type": 0.76,
}


@dataclass(frozen=True, slots=True)
class _Candidate:
    field_name: str
    confidence: float
    evidence_sources: tuple[str, ...]


class FieldDetector:
    """Map controls using several independent hints, never a single CSS layout."""

    def __init__(self, *, confidence_threshold: float = 0.75) -> None:
        if not 0.0 < confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        self.confidence_threshold = confidence_threshold

    @staticmethod
    def _ignored(control: FormControl) -> bool:
        return (
            control.disabled
            or not control.visible
            or control.input_type in {"hidden", "submit", "button", "reset", "image"}
            or control.tag in {"button"}
        )

    @staticmethod
    def _sources(control: FormControl) -> dict[str, str]:
        return {
            "label": control.label,
            "placeholder": control.placeholder,
            "name": control.name,
            "id": control.element_id,
            "aria_label": control.aria_label,
            "aria_description": control.aria_description,
            "nearby_text": control.nearby_text,
        }

    def _candidates(self, control: FormControl) -> tuple[_Candidate, ...]:
        evidence: dict[str, list[tuple[str, float]]] = {}

        autocomplete_field = AUTOCOMPLETE_FIELDS.get(control.autocomplete)
        if autocomplete_field:
            evidence.setdefault(autocomplete_field, []).append(
                ("autocomplete", SOURCE_WEIGHTS["autocomplete"])
            )

        if control.input_type == "email":
            evidence.setdefault("email", []).append(("input_type", SOURCE_WEIGHTS["input_type"]))
        elif control.input_type == "tel":
            evidence.setdefault("phone", []).append(("input_type", SOURCE_WEIGHTS["input_type"]))

        compact_sources = {
            source: _compact(raw_value)
            for source, raw_value in self._sources(control).items()
            if str(raw_value or "").strip()
        }
        for field_name, patterns in FIELD_PATTERNS.items():
            compact_patterns = tuple(_compact(pattern) for pattern in patterns)
            for source, value in compact_sources.items():
                if any(pattern and pattern in value for pattern in compact_patterns):
                    evidence.setdefault(field_name, []).append((source, SOURCE_WEIGHTS[source]))

        # A file input is only safe to classify when surrounding hints say CV/resume.
        if control.input_type == "file" and "resume" in evidence:
            evidence["resume"].append(("input_type", 0.88))

        candidates: list[_Candidate] = []
        for field_name, observations in evidence.items():
            sources = tuple(dict.fromkeys(source for source, _ in observations))
            confidence = min(1.0, max(weight for _, weight in observations) + 0.025 * (len(sources) - 1))
            candidates.append(_Candidate(field_name, confidence, sources))
        return tuple(sorted(candidates, key=lambda item: item.confidence, reverse=True))

    def detect(self, control: FormControl | Mapping[str, object]) -> FieldMatch | None:
        if not isinstance(control, FormControl):
            control = FormControl.from_mapping(control)
        if self._ignored(control):
            return None

        candidates = self._candidates(control)
        if not candidates:
            return FieldMatch(
                selector=control.selector,
                canonical_field=None,
                confidence=0.0,
                evidence_sources=(),
                sensitive=False,
                needs_user_confirmation=True,
                required=control.required,
                reason="unknown_field",
            )

        # If a control contains a sensitive hint, choose the strongest sensitive
        # interpretation even when a generic word such as "name" is also present.
        sensitive = [item for item in candidates if item.field_name in SENSITIVE_FIELDS]
        best = sensitive[0] if sensitive and sensitive[0].confidence >= 0.60 else candidates[0]
        close_second = next(
            (
                item
                for item in candidates
                if item.field_name != best.field_name
                and abs(item.confidence - best.confidence) <= 0.04
            ),
            None,
        )
        is_sensitive = best.field_name in SENSITIVE_FIELDS
        below_threshold = best.confidence < self.confidence_threshold
        ambiguous = close_second is not None
        needs_answer_generator = best.field_name in ANSWER_GENERATOR_FIELDS
        needs_confirmation = (
            is_sensitive or below_threshold or ambiguous or needs_answer_generator
        )
        if is_sensitive:
            reason = "sensitive_field"
        elif needs_answer_generator:
            reason = "answer_generator_required"
        elif ambiguous:
            reason = "ambiguous_field"
        elif below_threshold:
            reason = "low_confidence"
        else:
            reason = "matched"
        return FieldMatch(
            selector=control.selector,
            canonical_field=best.field_name,
            confidence=best.confidence,
            evidence_sources=best.evidence_sources,
            sensitive=is_sensitive,
            needs_user_confirmation=needs_confirmation,
            required=control.required,
            reason=reason,
        )

    def detect_many(
        self, controls: Sequence[FormControl | Mapping[str, object]]
    ) -> tuple[FieldMatch, ...]:
        matches: list[FieldMatch] = []
        for control in coerce_controls(controls):
            match = self.detect(control)
            if match is not None:
                matches.append(match)
        return tuple(matches)


__all__ = [
    "FIELD_PATTERNS",
    "ANSWER_GENERATOR_FIELDS",
    "FILLABLE_FIELDS",
    "PUBLIC_AUTOFILL_FIELDS",
    "SENSITIVE_FIELDS",
    "FieldDetector",
]
