"""Pydantic domain models shared by the MVP services.

Unknown resume facts are represented by ``None``.  Empty collections are only
used for workflow output (for example, review warnings), never to imply that a
candidate definitely has no education or experience.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class DomainModel(BaseModel):
    """Base model with safe, integration-friendly defaults."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class Education(DomainModel):
    school: str | None = None
    degree: str | None = None
    major: str | None = None
    location: str | None = None
    start_date: str | None = None
    graduation_date: str | None = None
    gpa: str | None = None
    description: str | None = None


class Experience(DomainModel):
    company: str | None = None
    title: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    description: str | None = None
    achievements: list[str] | None = None
    skills: list[str] | None = None


class Project(DomainModel):
    name: str | None = None
    role: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    description: str | None = None
    achievements: list[str] | None = None
    technologies: list[str] | None = None
    url: str | None = None


class Award(DomainModel):
    name: str | None = None
    issuer: str | None = None
    date: str | None = None
    description: str | None = None


class Certification(DomainModel):
    name: str | None = None
    issuer: str | None = None
    issue_date: str | None = None
    expiry_date: str | None = None
    credential_id: str | None = None
    url: str | None = None


class Language(DomainModel):
    name: str | None = None
    proficiency: str | None = None


class CandidateProfile(DomainModel):
    """Structured candidate facts extracted from a resume.

    The convenience fields ``school``, ``degree``, ``major`` and
    ``graduation_date`` mirror the primary education entry.  They make generic
    form mapping straightforward while retaining the full ``education`` list.
    """

    name: str | None = None
    phone: str | None = None
    email: str | None = None
    location: str | None = None

    education: list[Education] | None = None
    school: str | None = None
    degree: str | None = None
    major: str | None = None
    graduation_date: str | None = None

    skills: list[str] | None = None
    languages: list[Language] | None = None
    certifications: list[Certification] | None = None
    internships: list[Experience] | None = None
    work_experience: list[Experience] | None = None
    projects: list[Project] | None = None
    awards: list[Award] | None = None

    self_introduction: str | None = None
    target_roles: list[str] | None = None
    target_locations: list[str] | None = None
    career_stage: Literal["fresh_graduate", "internship", "experienced"] | None = None
    expected_salary: str | None = None
    available_start_date: str | None = None

    parse_method: Literal["local", "openai", "demo"] = "local"
    needs_user_confirmation: list[str] = Field(default_factory=list)
    parse_warnings: list[str] = Field(default_factory=list)

    @field_validator(
        "skills", "target_roles", "target_locations", mode="after"
    )
    @classmethod
    def _deduplicate_strings(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            cleaned = item.strip()
            key = cleaned.casefold()
            if cleaned and key not in seen:
                seen.add(key)
                result.append(cleaned)
        return result or None

    @model_validator(mode="after")
    def _sync_primary_education(self) -> "CandidateProfile":
        primary = self.education[0] if self.education else None
        if primary:
            self.school = self.school or primary.school
            self.degree = self.degree or primary.degree
            self.major = self.major or primary.major
            self.graduation_date = self.graduation_date or primary.graduation_date
        elif any((self.school, self.degree, self.major, self.graduation_date)):
            self.education = [
                Education(
                    school=self.school,
                    degree=self.degree,
                    major=self.major,
                    graduation_date=self.graduation_date,
                )
            ]
        return self


class JobPosting(DomainModel):
    id: int | str | None = None
    job_id: str | None = None
    title: str
    company: str
    location: str | None = None
    job_type: str | None = None
    department: str | None = None
    description: str = ""
    requirements: str | list[str] | None = None
    job_url: str = Field(
        default="",
        validation_alias=AliasChoices("job_url", "url"),
    )
    source: str | None = None
    publish_date: str | None = None
    required_skills: list[str] | None = None
    preferred_skills: list[str] | None = None

    @property
    def url(self) -> str:
        """Compatibility alias used by browser adapters."""

        return self.job_url

    def searchable_text(self) -> str:
        requirements = (
            "\n".join(self.requirements)
            if isinstance(self.requirements, list)
            else (self.requirements or "")
        )
        return "\n".join(
            part
            for part in (
                self.title,
                self.company,
                self.location,
                self.department,
                self.description,
                requirements,
                " ".join(self.required_skills or []),
                " ".join(self.preferred_skills or []),
            )
            if part
        )


class MatchAssessment(DomainModel):
    match_score: float = Field(ge=0, le=100)
    match_level: str | None = None
    base_score: float | None = Field(default=None, ge=0, le=100)
    llm_adjustment: float = Field(default=0, ge=-5, le=5)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "skills": 35.0,
            "experience": 25.0,
            "education": 15.0,
            "projects": 10.0,
            "location": 5.0,
            "target_role": 10.0,
        }
    )
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    advantages: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    reason: str = ""
    recommendation: str = ""

    @model_validator(mode="after")
    def _derive_labels(self) -> "MatchAssessment":
        self.base_score = self.match_score if self.base_score is None else self.base_score
        if not self.match_level:
            self.match_level = match_level_for_score(self.match_score)
        if not self.recommendation:
            self.recommendation = recommendation_for_score(self.match_score)
        return self


def match_level_for_score(score: float) -> str:
    if score >= 90:
        return "强烈推荐"
    if score >= 80:
        return "推荐"
    if score >= 70:
        return "可以投"
    if score >= 60:
        return "谨慎"
    return "默认隐藏"


def recommendation_for_score(score: float) -> str:
    if score >= 80:
        return "投递"
    if score >= 70:
        return "可以投递"
    if score >= 60:
        return "核实差距后再决定"
    return "暂不推荐"


class FieldSchema(DomainModel):
    field_id: str | None = None
    name: str | None = None
    label: str
    field_type: str = Field(
        default="text", validation_alias=AliasChoices("field_type", "type")
    )
    required: bool = False
    mapped_field: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    value: Any | None = None
    options: list[str] | None = None
    needs_user_confirmation: bool = False
    reason: str | None = None

    @model_validator(mode="after")
    def _enforce_confidence_gate(self) -> "FieldSchema":
        if self.confidence < 0.60:
            self.needs_user_confirmation = True
            self.reason = self.reason or "字段语义置信度低于 0.60"
        return self


class ApplicationReview(DomainModel):
    application_id: int | str | None = None
    company: str
    job_title: str
    match_score: float | None = Field(default=None, ge=0, le=100)
    checks: dict[str, bool] = Field(default_factory=dict)
    required_field_missing: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    needs_user_confirmation: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ready_to_submit: bool = False
    status: Literal["waiting_user", "ready_to_submit"] = "waiting_user"

    @model_validator(mode="after")
    def _enforce_submit_gate(self) -> "ApplicationReview":
        failed_checks = [name for name, passed in self.checks.items() if not passed]
        for field_name in failed_checks:
            if field_name not in self.missing_fields:
                self.missing_fields.append(field_name)
        self.required_field_missing = bool(
            self.required_field_missing or self.missing_fields
        )
        self.ready_to_submit = not (
            self.required_field_missing or self.needs_user_confirmation
        )
        self.status = "ready_to_submit" if self.ready_to_submit else "waiting_user"
        return self


class GeneratedAnswer(DomainModel):
    question: str
    answer: str | None = None
    needs_user_confirmation: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    source: Literal["local", "openai"] = "local"

    @model_validator(mode="after")
    def _never_answer_when_confirmation_is_required(self) -> "GeneratedAnswer":
        if self.needs_user_confirmation:
            self.answer = None
            self.confidence = min(self.confidence, 0.59)
        elif not self.answer:
            self.needs_user_confirmation = True
            self.confidence = min(self.confidence, 0.59)
        return self


# Explicit aliases make the public API readable without breaking integrations
# that already use the more verbose record names.
EducationRecord = Education
ExperienceRecord = Experience
ProjectRecord = Project
AwardRecord = Award
CertificationRecord = Certification
LanguageRecord = Language
