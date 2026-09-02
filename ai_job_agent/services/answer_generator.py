"""Truthful answers for recruiting-site open questions.

Questions that require an unknown personal decision or fact are never guessed.
They return ``needs_user_confirmation=True`` so the browser workflow can pause.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable

from pydantic import BaseModel

from ai_job_agent.models import CandidateProfile, GeneratedAnswer, JobPosting
from ai_job_agent.services.job_matcher import _canonical_skills


_UNKNOWN_DECISION_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("identity_number", ("身份证", "证件号码", "identity number", "id number", "passport number")),
    ("political_status", ("政治面貌", "political status")),
    ("marital_status", ("婚姻", "marital")),
    ("gender", ("性别", "gender", "sex")),
    ("nationality", ("国籍", "nationality", "citizenship")),
    ("date_of_birth", ("出生日期", "生日", "date of birth", "birthday", "年龄", " age")),
    ("salary_preference", ("期望薪资", "薪资要求", "expected salary", "salary expectation", "desired salary")),
    ("transfer_preference", ("接受调剂", "调剂", "reassignment", "transfer to another role")),
    ("relative_employment", ("亲属任职", "亲属在", "relative employed", "family member work")),
    ("non_compete", ("竞业", "non-compete", "noncompete")),
    ("travel_preference", ("接受出差", "出差", "business travel", "willing to travel")),
    ("relocation_preference", ("接受异地", "愿意搬迁", "relocate", "relocation")),
    ("visa_status", ("签证", "visa", "sponsorship")),
    ("work_authorization", ("工作许可", "合法工作", "work permit", "work authorization", "authorized to work")),
    ("availability", ("到岗", "入职时间", "start date", "availability", "available to start")),
    ("gpa", ("gpa", "绩点", "平均成绩")),
)

_STORY_PATTERNS = (
    "最大的失败",
    "最大失败",
    "弱点",
    "缺点",
    "冲突",
    "困难的决定",
    "领导力案例",
    "team conflict",
    "failure",
    "weakness",
    "difficult decision",
    "leadership example",
)


def _matches(question: str, terms: tuple[str, ...]) -> bool:
    lowered = question.casefold()
    return any(term.casefold() in lowered for term in terms)


def _first_education_value(
    profile: CandidateProfile, getter: Callable[[Any], str | None]
) -> str | None:
    for education in profile.education or []:
        value = getter(education)
        if value:
            return value
    return None


def _known_personal_value(profile: CandidateProfile, field: str) -> str | None:
    if field == "salary_preference":
        return profile.expected_salary
    if field == "availability":
        return profile.available_start_date
    if field == "gpa":
        return _first_education_value(profile, lambda education: education.gpa)
    return None


def _direct_fact(question: str, profile: CandidateProfile) -> tuple[str, str | None] | None:
    mappings: tuple[tuple[str, tuple[str, ...], str | None], ...] = (
        ("name", ("姓名", "full name", "your name"), profile.name),
        ("phone", ("手机号", "联系电话", "phone number", "mobile number"), profile.phone),
        ("email", ("邮箱", "电子邮件", "email address", "e-mail"), profile.email),
        ("school", ("学校", "院校", "university", "school name"), profile.school),
        ("degree", ("学历", "学位", "highest degree", "degree"), profile.degree),
        ("major", ("专业", "major", "field of study"), profile.major),
        (
            "graduation_date",
            ("毕业时间", "毕业日期", "graduation date"),
            profile.graduation_date,
        ),
        ("location", ("所在地", "现居地", "current location", "city"), profile.location),
    )
    for field, terms, value in mappings:
        if _matches(question, terms):
            return field, value
    return None


def _experience_evidence(profile: CandidateProfile) -> tuple[str | None, list[str]]:
    items = (profile.internships or []) + (profile.work_experience or [])
    if not items:
        return None, []
    item = items[0]
    evidence = [value for value in (item.company, item.title, item.description) if value]
    if item.company and item.title:
        return f"我曾在{item.company}担任{item.title}", evidence
    if item.title:
        return f"我的简历包含{item.title}相关经历", evidence
    if item.company:
        return f"我的简历记录了在{item.company}的经历", evidence
    if item.description:
        return f"我的相关经历包括：{item.description}", evidence
    return None, []


def _project_answer(profile: CandidateProfile) -> GeneratedAnswer | None:
    if not profile.projects:
        return None
    project = profile.projects[0]
    if not project.name and not project.description:
        return None
    pieces = [f"我想介绍{project.name}项目。" if project.name else "我想介绍简历中的一个项目。"]
    evidence = [value for value in (project.name, project.role, project.description) if value]
    if project.role:
        pieces.append(f"我在项目中的角色是{project.role}。")
    if project.description:
        pieces.append(project.description.rstrip("。") + "。")
    if project.technologies:
        pieces.append("项目使用了" + "、".join(project.technologies[:5]) + "。")
        evidence.extend(project.technologies[:5])
    if project.achievements:
        pieces.append(project.achievements[0].rstrip("。") + "。")
        evidence.append(project.achievements[0])
    pieces.append("这段经历让我能够基于真实项目讨论自己的工作方法和收获。")
    return GeneratedAnswer(
        question="",
        answer="".join(pieces),
        confidence=0.9,
        evidence=evidence,
    )


def _motivation_answer(
    question: str, profile: CandidateProfile, job: JobPosting | None
) -> GeneratedAnswer | None:
    if job is None and not profile.target_roles:
        return None
    role = job.title if job else profile.target_roles[0]
    company = job.company if job else None
    job_skills = _canonical_skills(job.searchable_text(), job.required_skills) if job else set()
    profile_skills = _canonical_skills("\n".join(profile.skills or []), profile.skills)
    relevant = sorted(job_skills & profile_skills)
    shown_skills = relevant or list(profile.skills or [])[:4]
    experience_sentence, experience_evidence = _experience_evidence(profile)

    opening = (
        f"我申请{company}的{role}，是因为这个岗位方向与我的求职目标和现有能力较为契合。"
        if company
        else f"我希望申请{role}，因为这个岗位方向与我的求职目标和现有能力较为契合。"
    )
    pieces = [opening]
    evidence: list[str] = [role]
    if company:
        evidence.append(company)
    if shown_skills:
        pieces.append("我的简历明确列出了" + "、".join(shown_skills) + "等技能。")
        evidence.extend(shown_skills)
    if experience_sentence:
        pieces.append(experience_sentence + "，这为理解岗位中的实际任务提供了基础。")
        evidence.extend(experience_evidence)
    pieces.append(
        "我希望在真实业务场景中继续提升相关能力，并以严谨、可验证的方式完成工作。"
        "如果有机会加入，我会先理解团队目标，再把已有技能落实到具体任务和持续复盘中。"
    )
    return GeneratedAnswer(
        question=question,
        answer="".join(pieces),
        confidence=0.84,
        evidence=evidence,
    )


def _strength_answer(question: str, profile: CandidateProfile) -> GeneratedAnswer | None:
    skills = list(profile.skills or [])[:5]
    experience_sentence, experience_evidence = _experience_evidence(profile)
    if not skills and not experience_sentence:
        return None
    pieces = ["我的优势是能够把已有知识与实际任务结合，并保持基于事实的沟通。"]
    evidence: list[str] = []
    if skills:
        pieces.append("简历中明确体现的能力包括" + "、".join(skills) + "。")
        evidence.extend(skills)
    if experience_sentence:
        pieces.append(experience_sentence + "。")
        evidence.extend(experience_evidence)
    pieces.append("面对新任务时，我会先确认目标和约束，再拆解问题、验证结果并及时同步风险。")
    return GeneratedAnswer(
        question=question,
        answer="".join(pieces),
        confidence=0.82,
        evidence=evidence,
    )


def _introduction_answer(question: str, profile: CandidateProfile) -> GeneratedAnswer | None:
    facts: list[str] = []
    pieces: list[str] = []
    if profile.name:
        pieces.append(f"我叫{profile.name}。")
        facts.append(profile.name)
    if profile.school or profile.major or profile.degree:
        education = "，".join(
            value for value in (profile.school, profile.major, profile.degree) if value
        )
        pieces.append(f"我的教育背景是{education}。")
        facts.extend(value for value in (profile.school, profile.major, profile.degree) if value)
    if profile.skills:
        pieces.append("我掌握" + "、".join(profile.skills[:5]) + "。")
        facts.extend(profile.skills[:5])
    experience_sentence, experience_evidence = _experience_evidence(profile)
    if experience_sentence:
        pieces.append(experience_sentence + "。")
        facts.extend(experience_evidence)
    if not pieces:
        return None
    pieces.append("我希望在岗位中继续把这些已有能力用于真实问题，并通过反馈不断改进。")
    return GeneratedAnswer(
        question=question,
        answer="".join(pieces),
        confidence=0.88,
        evidence=facts,
    )


def _career_answer(
    question: str, profile: CandidateProfile, job: JobPosting | None
) -> GeneratedAnswer | None:
    role = profile.target_roles[0] if profile.target_roles else (job.title if job else None)
    if not role:
        return None
    skills = list(profile.skills or [])[:3]
    answer = (
        f"我希望近期围绕{role}方向打牢专业基础，先在真实项目中理解业务目标、"
        "交付标准和团队协作方式。中期我会持续复盘项目结果，补足岗位需要的能力，"
        "逐步承担更完整的任务。长期目标是成为能够把专业能力与实际业务问题结合、"
        "并持续创造可验证价值的从业者。"
    )
    evidence = [role]
    if skills:
        answer += "我会从已有的" + "、".join(skills) + "能力出发继续学习。"
        evidence.extend(skills)
    return GeneratedAnswer(
        question=question,
        answer=answer,
        confidence=0.78,
        evidence=evidence,
    )


def _fit_length(text: str, char_limit: int | None) -> str:
    if char_limit is None or len(text) <= char_limit:
        return text
    if char_limit <= 1:
        return text[:char_limit]
    shortened = text[: char_limit - 1].rstrip("，,；;。 .")
    return shortened + "。"


class _LLMAnswer(BaseModel):
    answer: str


def _safe_llm_rewrite(candidate: str, source: str) -> bool:
    # New numerical claims are the most damaging common embellishment.  Reject
    # them, as well as a small set of unsupported boastful phrases.
    if any(number not in source for number in re.findall(r"\d+(?:\.\d+)?%?", candidate)):
        return False
    boastful = ("多年经验", "丰富经验", "深厚经验", "主导了", "独立负责", "显著提升", "大幅提升")
    if any(phrase in candidate and phrase not in source for phrase in boastful):
        return False
    return True


class AnswerGenerator:
    def __init__(
        self,
        *,
        use_openai: bool | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.client = client
        self.use_openai = (
            bool(os.getenv("OPENAI_API_KEY")) if use_openai is None else use_openai
        )
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-5-mini")

    def generate(
        self,
        question: str,
        profile: CandidateProfile,
        job: JobPosting | None = None,
        *,
        char_limit: int | None = None,
    ) -> GeneratedAnswer:
        if not question or not question.strip():
            raise ValueError("question cannot be empty")

        for field, terms in _UNKNOWN_DECISION_PATTERNS:
            if _matches(question, terms):
                known = _known_personal_value(profile, field)
                if known:
                    return GeneratedAnswer(
                        question=question,
                        answer=_fit_length(known, char_limit),
                        confidence=0.99,
                        evidence=[known],
                    )
                return GeneratedAnswer(
                    question=question,
                    needs_user_confirmation=True,
                    missing_fields=[field],
                    confidence=0,
                )

        direct = _direct_fact(question, profile)
        if direct is not None:
            field, value = direct
            if value:
                return GeneratedAnswer(
                    question=question,
                    answer=_fit_length(value, char_limit),
                    confidence=0.99,
                    evidence=[value],
                )
            return GeneratedAnswer(
                question=question,
                needs_user_confirmation=True,
                missing_fields=[field],
                confidence=0,
            )

        if _matches(question, _STORY_PATTERNS):
            return GeneratedAnswer(
                question=question,
                needs_user_confirmation=True,
                missing_fields=["personal_example"],
                confidence=0,
            )

        result: GeneratedAnswer | None
        if _matches(
            question,
            (
                "为什么申请",
                "为什么选择",
                "申请原因",
                "why do you want",
                "why are you applying",
                "why this role",
                "why this company",
                "motivation",
            ),
        ):
            result = _motivation_answer(question, profile, job)
        elif _matches(question, ("项目", "project")):
            result = _project_answer(profile)
            if result:
                result.question = question
        elif _matches(question, ("优势", "优点", "strength", "best qualities")):
            result = _strength_answer(question, profile)
        elif _matches(
            question,
            ("自我介绍", "介绍自己", "tell us about yourself", "introduce yourself"),
        ):
            result = _introduction_answer(question, profile)
        elif _matches(question, ("职业规划", "职业目标", "career plan", "career goal")):
            result = _career_answer(question, profile, job)
        else:
            result = None

        if result is None or not result.answer:
            return GeneratedAnswer(
                question=question,
                needs_user_confirmation=True,
                missing_fields=["answer_to_open_question"],
                confidence=0,
            )

        result.answer = _fit_length(result.answer, char_limit)
        return self._maybe_rewrite(result, char_limit)

    def _maybe_rewrite(
        self, result: GeneratedAnswer, char_limit: int | None
    ) -> GeneratedAnswer:
        if not self.use_openai or not result.answer:
            return result
        if self.client is None and not os.getenv("OPENAI_API_KEY"):
            return result
        try:
            client = self.client
            if client is None:
                from openai import OpenAI

                client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
            source = "\n".join([result.answer, *result.evidence])
            response = client.responses.parse(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "润色求职表单回答。只能重写给定草稿，不得添加任何经历、数字、"
                            "成就、技能或公司信息。语气自然简洁；证据不足时原样返回。"
                        ),
                    },
                    {"role": "user", "content": source},
                ],
                text_format=_LLMAnswer,
                store=False,
            )
            parsed = response.output_parsed
            if parsed is None:
                return result
            if not isinstance(parsed, _LLMAnswer):
                parsed = _LLMAnswer.model_validate(parsed)
            answer = _fit_length(parsed.answer.strip(), char_limit)
            if not answer or not _safe_llm_rewrite(answer, source):
                return result
            result.answer = answer
            result.source = "openai"
            return result
        except Exception:
            return result


def generate_answer(
    question: str,
    profile: CandidateProfile,
    job: JobPosting | None = None,
    *,
    char_limit: int | None = None,
    use_openai: bool | None = None,
    model: str | None = None,
    client: Any | None = None,
) -> GeneratedAnswer:
    return AnswerGenerator(
        use_openai=use_openai, model=model, client=client
    ).generate(question, profile, job, char_limit=char_limit)


__all__ = ["AnswerGenerator", "generate_answer"]
