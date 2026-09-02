from __future__ import annotations

import io
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_assistant.autofill import (
    AutofillError,
    ConsentRequired,
    InvalidApplicantProfile,
    InvalidResume,
    UnsafeApplicationUrl,
    create_autofill_plan,
    normalize_profile,
    validate_greenhouse_application_url,
)
from job_assistant.autofill_runner import (
    canonical_field,
    choose_apply_navigation_target,
    detect_form_blockers,
    hard_stop_blockers,
    start_autofill_process,
)
import job_assistant.autofill_runner as runner


PROFILE = {
    "first_name": "Xiao",
    "last_name": "Chen",
    "email": "xiao.chen@example.com",
    "phone": "+86 138 0000 0000",
    "location": "Shanghai, China",
    "full_name": "Xiao Chen",
    "school": "Example University",
    "degree": "Bachelor",
    "major": "Mechanical Engineering",
    "graduation_date": "2027-06",
    "national_id": "must-never-be-copied",
}


@pytest.fixture()
def resume_path(tmp_path: Path) -> Path:
    path = tmp_path / "English Resume.pdf"
    path.write_bytes(b"%PDF-1.4\nminimal offline fixture")
    return path


def test_accepts_official_and_employer_greenhouse_absolute_urls():
    assert (
        validate_greenhouse_application_url(
            "https://job-boards.greenhouse.io/acme/jobs/123?source=site"
        )
        == "https://job-boards.greenhouse.io/acme/jobs/123?source=site"
    )
    assert (
        validate_greenhouse_application_url(
            "https://careers.example.com/jobs/greenhouse/123"
        )
        == "https://careers.example.com/jobs/greenhouse/123"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://boards.greenhouse.io/acme/jobs/1",
        "javascript:alert(1)",
        "https://localhost/jobs/1",
        "https://127.0.0.1/jobs/1",
        "https://user:secret@careers.example.com/jobs/1",
        "https://careers.example.com:8443/jobs/1",
        "https://careers.example.com/%0d%0aHeader:value",
    ],
)
def test_rejects_unsafe_application_urls(url: str):
    with pytest.raises(UnsafeApplicationUrl):
        validate_greenhouse_application_url(url)


def test_consent_argument_is_mandatory_even_for_dry_run(resume_path: Path):
    with pytest.raises(TypeError):
        create_autofill_plan(  # type: ignore[call-arg]
            "https://boards.greenhouse.io/acme/jobs/1", PROFILE, resume_path
        )


def test_dry_run_builds_public_plan_without_consent(resume_path: Path):
    plan = create_autofill_plan(
        "https://boards.greenhouse.io/acme/jobs/1",
        PROFILE,
        resume_path,
        consent=False,
    )
    assert plan.dry_run is True
    assert plan.fields == {
        key: PROFILE[key]
        for key in (
            "first_name",
            "last_name",
            "email",
            "phone",
            "full_name",
            "location",
            "school",
            "degree",
            "major",
            "graduation_date",
        )
    }
    summary = plan.public_summary()
    assert summary["will_submit"] is False
    assert summary["resume_selected"] is True
    assert summary["resume_type"] == ".pdf"
    serialized_summary = json.dumps(summary)
    assert PROFILE["email"] not in serialized_summary
    assert PROFILE["phone"] not in serialized_summary
    assert resume_path.name not in serialized_summary
    assert str(resume_path.parent) not in serialized_summary
    assert PROFILE["email"] not in repr(plan)
    assert str(resume_path) not in repr(plan)


def test_execution_requires_explicit_true_consent(resume_path: Path):
    with pytest.raises(ConsentRequired):
        create_autofill_plan(
            "https://boards.greenhouse.io/acme/jobs/1",
            PROFILE,
            resume_path,
            consent=False,
            dry_run=False,
        )
    with pytest.raises(ConsentRequired):
        create_autofill_plan(
            "https://boards.greenhouse.io/acme/jobs/1",
            PROFILE,
            resume_path,
            consent="yes",  # type: ignore[arg-type]
            dry_run=False,
        )


def test_rejects_untrusted_source_family(resume_path: Path):
    with pytest.raises(AutofillError, match="Greenhouse"):
        create_autofill_plan(
            "https://careers.example.com/jobs/1",
            PROFILE,
            resume_path,
            source="Unknown scraper",
            consent=False,
        )


@pytest.mark.parametrize(
    "source",
    (
        "Lever:acme",
        "Ashby:acme",
        "SmartRecruiters:acme",
        "Li Auto Campus",
        "OfficialCareer:careers.example.com",
    ),
)
def test_accepts_supported_non_greenhouse_sources(resume_path: Path, source: str):
    plan = create_autofill_plan(
        "https://careers.example.com/jobs/1",
        PROFILE,
        resume_path,
        source=source,
        consent=False,
    )
    assert plan.source == source
    assert plan.public_summary()["will_submit"] is False


def test_apply_navigation_uses_exact_apply_control_and_never_submit():
    target = choose_apply_navigation_target(
        [
            {
                "dom_index": 0,
                "tag": "button",
                "text": "Submit Application",
                "visible": True,
            },
            {
                "dom_index": 1,
                "tag": "a",
                "text": "Apply now",
                "href": "https://jobs.example.com/roles/1/apply",
                "visible": True,
            },
            {
                "dom_index": 2,
                "tag": "a",
                "text": "Explore our culture",
                "href": "https://jobs.example.com/culture",
                "visible": True,
            },
        ]
    )

    assert target is not None
    assert target.dom_index == 1
    assert target.href.endswith("/apply")


def test_apply_navigation_rejects_fuzzy_or_final_submit_controls():
    assert choose_apply_navigation_target(
        [
            {"dom_index": 0, "tag": "button", "text": "Submit", "visible": True},
            {"dom_index": 1, "tag": "a", "text": "Apply filters", "visible": True},
        ]
    ) is None


def test_join_intention_is_an_apply_transition_but_final_submit_texts_are_blocked():
    final_submit_texts = (
        "Submit",
        "Submit Application",
        "Send Application",
        "提交",
        "提交申请",
        "确认提交",
    )
    submit_controls = [
        {
            "dom_index": index,
            "tag": "button",
            "text": text,
            "visible": True,
        }
        for index, text in enumerate(final_submit_texts)
    ]
    intention_control = {
        "dom_index": len(submit_controls),
        "tag": "button",
        "text": "  加入意向单  ",
        "visible": True,
    }

    target = choose_apply_navigation_target([*submit_controls, intention_control])

    assert target is not None
    assert target.dom_index == intention_control["dom_index"]
    assert target.tag == "button"
    assert choose_apply_navigation_target(submit_controls) is None


def test_start_application_is_safe_transition_but_resume_submit_is_not():
    target = choose_apply_navigation_target(
        [
            {
                "dom_index": 0,
                "tag": "button",
                "text": "投递简历",
                "visible": True,
            },
            {
                "dom_index": 1,
                "tag": "button",
                "text": "开始投递",
                "visible": True,
            },
        ]
    )

    assert target is not None
    assert target.dom_index == 1


def test_accepts_applicant_profile_style_attributes_and_ignores_extras():
    class ApplicantProfileLike:
        first_name = "Ada"
        last_name = "Lovelace"
        email = "ada@example.com"
        phone = "+44 20 0000 0000"
        location = "London"
        application_fields = {"passport": "do-not-copy"}

    values = dict(normalize_profile(ApplicantProfileLike()))
    assert values["first_name"] == "Ada"
    assert set(values) == {"first_name", "last_name", "email", "phone", "location"}
    assert "passport" not in values


def test_normalizes_known_multiline_profile_fields_and_excludes_unknown_data():
    profile = {
        **PROFILE,
        "education_summary": "  Example University\r\nB.Eng.  \r\n",
        "skills_summary": " Python\tSQL \r\n",
        "language_skills": "\r\nMandarin\rEnglish  ",
        "certification_summary": " AWS Associate  \r\n",
        "internship_summary": " Acme Controls\r\nBuilt test rigs  ",
        "work_experience_summary": " Robotics Lab\rResearch assistant\t ",
        "project_summary": " Autonomous car\r\nOwned controls  ",
        "award_summary": " First prize \r\n",
        "professional_summary": " Engineer\r\nSafety focused  ",
        "passport_number": "must-never-be-copied",
        "salary_expectation": "must-never-be-copied",
        "visa_sponsorship": "must-never-be-copied",
        "free_form_answer": "must-never-be-copied",
    }

    values = dict(normalize_profile(profile))

    assert {
        field_name: values[field_name]
        for field_name in (
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
    } == {
        "education": "Example University\nB.Eng.",
        "skills": "Python\tSQL",
        "languages": "Mandarin\nEnglish",
        "certifications": "AWS Associate",
        "internships": "Acme Controls\nBuilt test rigs",
        "work_experience": "Robotics Lab\nResearch assistant",
        "projects": "Autonomous car\nOwned controls",
        "awards": "First prize",
        "self_introduction": "Engineer\nSafety focused",
    }
    assert {
        "national_id",
        "passport_number",
        "salary_expectation",
        "visa_sponsorship",
        "free_form_answer",
    }.isdisjoint(values)


def test_multiline_profile_fields_reject_unsafe_control_characters():
    with pytest.raises(InvalidApplicantProfile, match="projects"):
        normalize_profile({**PROFILE, "projects": "safe line\nunsafe\x00line"})


def test_current_location_is_optional_and_is_not_guessed(resume_path: Path):
    profile_without_location = dict(PROFILE, location="")

    values = dict(normalize_profile(profile_without_location))
    plan = create_autofill_plan(
        "https://careers.example.com/jobs/1",
        profile_without_location,
        resume_path,
        source="OfficialCareer:careers.example.com",
        consent=False,
    )

    assert "location" not in values
    assert "location" not in plan.fields
    assert "location" not in plan.public_summary()["fields_to_fill"]


def test_rejects_incomplete_profile_and_unsupported_resume(tmp_path: Path):
    incomplete = dict(PROFILE, phone="")
    with pytest.raises(InvalidApplicantProfile, match="phone"):
        normalize_profile(incomplete)

    text_resume = tmp_path / "resume.txt"
    text_resume.write_text("resume", encoding="utf-8")
    with pytest.raises(InvalidResume):
        create_autofill_plan(
            "https://boards.greenhouse.io/acme/jobs/1",
            PROFILE,
            text_resume,
            consent=False,
        )


@pytest.mark.parametrize(
    ("control", "expected"),
    [
        ({"name": "job_application[first_name]", "type": "text"}, "first_name"),
        ({"autocomplete": "family-name", "type": "text"}, "last_name"),
        ({"type": "email", "name": "job_application[email]"}, "email"),
        ({"type": "tel", "name": "job_application[phone]"}, "phone"),
        ({"id": "job_application_location", "type": "text"}, "location"),
        ({"name": "full_name", "label": "Full name", "type": "text"}, "full_name"),
        ({"name": "university", "label": "School", "type": "text"}, "school"),
        ({"id": "highest_degree", "label": "最高学历", "type": "text"}, "degree"),
        ({"name": "field_of_study", "label": "专业", "type": "text"}, "major"),
        ({"name": "graduation_date", "label": "毕业时间", "type": "month"}, "graduation_date"),
        ({"type": "file", "label": "Upload resume / CV"}, "resume"),
        ({"type": "file", "label": "Required cover letter"}, None),
        ({"type": "email", "name": "referrer_email"}, None),
        ({"type": "tel", "name": "emergency_phone"}, None),
        ({"type": "text", "name": "manager_first_name"}, None),
        ({"type": "checkbox", "label": "I agree"}, None),
    ],
)
def test_maps_only_allowlisted_form_controls(control: dict[str, object], expected: str | None):
    assert canonical_field(control) == expected


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("姓名", "full_name"),
        ("电子邮箱", "email"),
        ("联系电话", "phone"),
        ("现居地", "location"),
        ("毕业院校", "school"),
        ("最高学历", "degree"),
        ("所学专业", "major"),
        ("毕业日期", "graduation_date"),
        ("教育背景", "education"),
        ("实习经验", "internships"),
        ("工作经验", "work_experience"),
        ("项目经验", "projects"),
        ("个人总结", "self_introduction"),
    ],
)
def test_maps_chinese_profile_labels_to_canonical_fields(label: str, expected: str):
    assert canonical_field({"tag": "textarea", "type": "text", "label": label}) == expected


def test_maps_required_nearby_and_aria_labels_without_fuzzy_sensitive_matches():
    assert canonical_field(
        {"type": "text", "nearby_text": "姓名（必填）"}
    ) == "full_name"
    assert canonical_field(
        {"type": "email", "aria_label": "电子邮箱 *"}
    ) == "email"
    assert canonical_field(
        {"type": "text", "nearby_text": "紧急联系人姓名（必填）"}
    ) is None


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("national_id", "身份证号"),
        ("passport_number", "护照号码"),
        ("salary_expectation", "期望薪资"),
        ("visa_sponsorship", "是否需要签证担保"),
        ("custom_question", "请说明离职原因"),
    ],
)
def test_sensitive_and_unknown_controls_have_no_canonical_mapping(name: str, label: str):
    assert canonical_field({"tag": "input", "type": "text", "name": name, "label": label}) is None


def test_known_fields_are_filled_while_unknown_answer_stays_blank(
    resume_path: Path,
) -> None:
    controls = [
        {
            "dom_index": 0,
            "tag": "input",
            "type": "text",
            "name": "full_name",
            "label": "Full name",
            "visible": True,
        },
        {
            "dom_index": 1,
            "tag": "input",
            "type": "text",
            "name": "work_authorization",
            "label": "Describe your work authorization",
            "required": True,
            "visible": True,
        },
        {
            "dom_index": 2,
            "tag": "input",
            "type": "file",
            "name": "resume",
            "label": "Resume / CV",
            "visible": True,
        },
    ]

    class FakeLocator:
        def __init__(self, control: dict[str, object]) -> None:
            self.control = control
            self.value = ""

        def evaluate(self, _script: str) -> dict[str, object]:
            return dict(self.control)

        def fill(self, value: str) -> None:
            self.value = value

        def set_input_files(self, value: str) -> None:
            self.value = value

    locators = [FakeLocator(control) for control in controls]

    class AllControls:
        def nth(self, index: int) -> FakeLocator:
            return locators[index]

    class FakePage:
        def locator(self, _selector: str) -> AllControls:
            return AllControls()

    plan = create_autofill_plan(
        "https://careers.example.com/jobs/1",
        PROFILE,
        resume_path,
        source="OfficialCareer:careers.example.com",
        consent=True,
        dry_run=False,
    )
    filled = runner._fill_allowed_controls(
        FakePage(),
        {"controls": controls},
        plan,
    )

    assert filled == ("full_name", "resume")
    assert locators[0].value == PROFILE["full_name"]
    assert locators[1].value == ""
    assert locators[2].value == str(resume_path)


class _TrackedFormLocator:
    def __init__(self, control: dict[str, object]) -> None:
        self.control = dict(control)
        self.fill_calls: list[str] = []
        self.upload_calls: list[str] = []
        self.select_calls: list[str] = []

    def evaluate(self, _script: str) -> dict[str, object]:
        return dict(self.control)

    def fill(self, value: str) -> None:
        self.fill_calls.append(value)
        self.control["value"] = value

    def set_input_files(self, value: str) -> None:
        self.upload_calls.append(value)
        self.control["value"] = value

    def select_option(self, *, label: str) -> None:
        self.select_calls.append(label)
        self.control["value"] = label


class _TrackedFormPage:
    def __init__(self, controls: list[dict[str, object]]) -> None:
        self.locators = [_TrackedFormLocator(control) for control in controls]

    def locator(self, selector: str) -> object:
        assert selector == runner.CONTROL_SELECTOR
        locators = self.locators

        class AllControls:
            def nth(self, index: int) -> _TrackedFormLocator:
                return locators[index]

        return AllControls()


def test_existing_form_values_are_never_overwritten(resume_path: Path):
    controls = [
        {
            "dom_index": 0,
            "tag": "input",
            "type": "text",
            "name": "full_name",
            "label": "姓名",
            "value": "Applicant-entered name",
            "visible": True,
        }
    ]
    page = _TrackedFormPage(controls)
    plan = create_autofill_plan(
        "https://careers.example.com/jobs/1",
        PROFILE,
        resume_path,
        source="OfficialCareer:careers.example.com",
        consent=True,
        dry_run=False,
    )

    filled = runner._fill_allowed_controls(page, {"controls": controls}, plan)

    assert filled == ("full_name",)
    assert page.locators[0].control["value"] == "Applicant-entered name"
    assert page.locators[0].fill_calls == []
    assert page.locators[0].select_calls == []


@pytest.mark.parametrize(
    ("field_name", "label"),
    [
        ("education", "教育经历"),
        ("certifications", "资格证书"),
        ("internships", "实习经历"),
        ("work_experience", "工作经历"),
        ("projects", "项目经历"),
        ("awards", "获奖经历"),
        ("self_introduction", "自我介绍"),
    ],
)
def test_structured_summaries_fill_only_textareas(
    resume_path: Path, field_name: str, label: str
):
    controls = [
        {
            "dom_index": 0,
            "tag": "input",
            "type": "text",
            "name": field_name,
            "label": label,
            "value": "",
            "visible": True,
        },
        {
            "dom_index": 1,
            "tag": "textarea",
            "type": "",
            "name": field_name,
            "label": label,
            "value": "",
            "visible": True,
        },
    ]
    page = _TrackedFormPage(controls)
    plan = create_autofill_plan(
        "https://careers.example.com/jobs/1",
        {**PROFILE, field_name: "第一行\r\n第二行  "},
        resume_path,
        source="OfficialCareer:careers.example.com",
        consent=True,
        dry_run=False,
    )

    filled = runner._fill_allowed_controls(page, {"controls": controls}, plan)

    assert filled == (field_name,)
    assert page.locators[0].fill_calls == []
    assert page.locators[0].control["value"] == ""
    assert page.locators[1].fill_calls == ["第一行\n第二行"]


def test_resume_control_receives_the_validated_file_path(resume_path: Path):
    controls = [
        {
            "dom_index": 0,
            "tag": "input",
            "type": "file",
            "name": "resume",
            "label": "上传简历",
            "value": "",
            # Hidden native file inputs are commonly activated by a styled label.
            "visible": False,
        }
    ]
    page = _TrackedFormPage(controls)
    plan = create_autofill_plan(
        "https://careers.example.com/jobs/1",
        PROFILE,
        resume_path,
        source="OfficialCareer:careers.example.com",
        consent=True,
        dry_run=False,
    )

    filled = runner._fill_allowed_controls(page, {"controls": controls}, plan)

    assert filled == ("resume",)
    assert page.locators[0].upload_calls == [str(plan.resume_path)]
    assert page.locators[0].fill_calls == []


def test_browser_storage_state_path_is_stable_and_opaque(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    state_root = tmp_path / "browser_profiles"
    monkeypatch.setattr(runner, "_BROWSER_STATE_ROOT", state_root)
    first_url = (
        "https://careers.example.com/jobs/123"
        "?candidate=xiao.chen%40example.com&user_id=abc-123"
    )
    second_url = (
        "https://careers.example.com/jobs/456"
        "?candidate=someone.else%40example.com&user_id=xyz-456"
    )

    first_path = runner._browser_state_path(first_url)
    repeated_path = runner._browser_state_path(first_url)
    same_site_path = runner._browser_state_path(second_url)

    assert first_path == repeated_path == same_site_path
    relative = first_path.relative_to(state_root)
    assert relative.name == "state.json"
    opaque_key = relative.parent.name
    assert len(opaque_key) == 24
    assert all(character in "0123456789abcdef" for character in opaque_key)
    assert all(
        raw_value not in str(relative).casefold()
        for raw_value in (
            "careers.example.com",
            "jobs",
            "123",
            "456",
            "xiao.chen",
            "someone.else",
            "user_id",
        )
    )


def test_detects_captcha_login_and_special_required_question():
    snapshot = {
        "page_text": "Please verify you are human",
        "frame_urls": ["https://www.google.com/recaptcha/api2/anchor"],
        "controls": [
            {"type": "password", "visible": True},
            {
                "type": "text",
                "name": "visa_sponsorship",
                "label": "Will you need visa sponsorship?",
                "required": True,
                "visible": True,
            },
            {
                "type": "email",
                "name": "email",
                "label": "Email",
                "required": True,
                "visible": True,
            },
        ],
    }
    blockers = detect_form_blockers(snapshot)
    assert {blocker.code for blocker in blockers} == {
        "captcha",
        "login_required",
        "unsupported_required_question",
    }
    assert all("xiao.chen" not in blocker.message for blocker in blockers)


def test_optional_custom_questions_do_not_block():
    snapshot = {
        "page_text": "Apply for this role",
        "frame_urls": [],
        "controls": [
            {
                "type": "text",
                "name": "portfolio",
                "label": "Portfolio",
                "required": False,
                "visible": True,
            },
            {
                "type": "file",
                "name": "resume",
                "label": "Resume",
                "required": True,
                "visible": False,
            },
        ],
    }
    assert detect_form_blockers(snapshot) == ()


def test_only_captcha_and_login_are_prefill_hard_stops():
    snapshot = {
        "page_text": "Sign in to apply",
        "frame_urls": ["https://captcha.example.com/hcaptcha/challenge"],
        "controls": [
            {
                "type": "text",
                "name": "work_authorization",
                "label": "Work authorization *",
                "label_required": True,
                "visible": True,
            }
        ],
    }
    blockers = detect_form_blockers(snapshot)
    assert {blocker.code for blocker in hard_stop_blockers(blockers)} == {
        "captcha",
        "login_required",
    }
    assert "unsupported_required_question" not in {
        blocker.code for blocker in hard_stop_blockers(blockers)
    }


class _FakePage:
    url = "https://boards.greenhouse.io/acme/jobs/1"

    def goto(self, *_args: object, **_kwargs: object) -> None:
        pass

    def bring_to_front(self) -> None:
        pass

    def is_closed(self) -> bool:
        return True

    def wait_for_timeout(self, _milliseconds: int) -> None:
        pass


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.page = page

    def route(self, *_args: object, **_kwargs: object) -> None:
        pass

    def new_page(self) -> _FakePage:
        return self.page


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self.context = _FakeContext(page)

    def new_context(self) -> _FakeContext:
        return self.context

    def close(self) -> None:
        pass


class _FakePlaywright:
    def __init__(self, page: _FakePage) -> None:
        self.chromium = self
        self.page = page

    def launch(self, *, headless: bool) -> _FakeBrowser:
        assert headless is False
        return _FakeBrowser(self.page)


class _FakePlaywrightManager:
    def __init__(self, page: _FakePage) -> None:
        self.playwright = _FakePlaywright(page)

    def __enter__(self) -> _FakePlaywright:
        return self.playwright

    def __exit__(self, *_args: object) -> None:
        pass


def test_visible_browser_falls_back_to_system_edge_on_windows(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[dict[str, object]] = []
    edge_browser = object()

    class FallbackChromium:
        def launch(self, **kwargs: object) -> object:
            calls.append(kwargs)
            if "channel" not in kwargs:
                raise RuntimeError("bundled Chromium side-by-side error")
            assert kwargs["channel"] == "msedge"
            return edge_browser

    playwright = SimpleNamespace(chromium=FallbackChromium())
    monkeypatch.setattr(runner, "os", SimpleNamespace(name="nt"))

    browser, engine = runner._launch_visible_browser(playwright)

    assert browser is edge_browser
    assert engine == "system-edge"
    assert calls == [
        {"headless": False},
        {"headless": False, "channel": "msedge"},
    ]


def test_manual_review_window_returns_normally_when_page_is_closed():
    class AlreadyClosedPage:
        def bring_to_front(self) -> None:
            raise RuntimeError("Target page has been closed")

        def is_closed(self) -> bool:
            return True

    class ClosesWhileWaitingPage:
        def __init__(self) -> None:
            self.closed = False

        def bring_to_front(self) -> None:
            pass

        def is_closed(self) -> bool:
            return self.closed

        def wait_for_timeout(self, _milliseconds: int) -> None:
            self.closed = True
            raise RuntimeError("Target page has been closed")

    assert runner._wait_for_manual_review_window(AlreadyClosedPage()) is None
    assert runner._wait_for_manual_review_window(ClosesWhileWaitingPage()) is None


def test_human_verification_returns_none_when_page_closes_during_wait(
    monkeypatch: pytest.MonkeyPatch,
):
    class ClosesWhileWaitingPage:
        def __init__(self) -> None:
            self.closed = False

        def bring_to_front(self) -> None:
            pass

        def is_closed(self) -> bool:
            return self.closed

        def wait_for_timeout(self, _milliseconds: int) -> None:
            self.closed = True
            raise RuntimeError("Target page has been closed")

    monkeypatch.setattr(runner, "_emit", lambda *_args, **_kwargs: None)
    snapshot = {
        "page_text": "Verify you are human",
        "frame_urls": [],
        "controls": [],
    }

    updated_snapshot, blockers = runner._wait_for_human_verification(
        ClosesWhileWaitingPage(), snapshot
    )

    assert updated_snapshot is None
    assert {blocker.code for blocker in blockers} == {"captcha"}


def _actual_plan(resume_path: Path):
    return create_autofill_plan(
        "https://boards.greenhouse.io/acme/jobs/1",
        PROFILE,
        resume_path,
        consent=True,
        dry_run=False,
    )


def test_unknown_required_question_prefills_safe_fields_then_hands_over(
    monkeypatch: pytest.MonkeyPatch, resume_path: Path
):
    events: list[object] = []
    snapshot = {
        "page_text": "Apply",
        "frame_urls": [],
        "controls": [
            {
                "type": "text",
                "name": "job_application[first_name]",
                "label": "First name",
                "required": True,
                "visible": True,
            },
            {
                "type": "text",
                "name": "work_authorization",
                "label": "Describe your work authorization",
                "required": True,
                "visible": True,
            },
        ],
    }
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: _FakePlaywrightManager(_FakePage()),
    )
    monkeypatch.setattr(runner, "_collect_snapshot", lambda _page: snapshot)

    def fake_fill(_page: object, _snapshot: object, _plan: object):
        events.append("filled")
        return ("first_name", "resume")

    monkeypatch.setattr(runner, "_fill_allowed_controls", fake_fill)
    monkeypatch.setattr(
        runner,
        "_emit",
        lambda status, message, **details: events.append((status, details)),
    )

    assert runner.run_headed_autofill(_actual_plan(resume_path)) == 0
    assert events[0] == "filled"
    status, details = events[1]
    assert status == "manual_action_required"
    assert details["filled_fields"] == ["first_name", "resume"]
    assert any(
        blocker["code"] == "unsupported_required_question"
        for blocker in details["blockers"]
    )


def test_captcha_stops_before_any_field_is_filled(
    monkeypatch: pytest.MonkeyPatch, resume_path: Path
):
    emitted: list[str] = []
    snapshot = {
        "page_text": "Verify you are human",
        "frame_urls": [],
        "controls": [],
    }
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: _FakePlaywrightManager(_FakePage()),
    )
    monkeypatch.setattr(runner, "_collect_snapshot", lambda _page: snapshot)
    monkeypatch.setattr(
        runner,
        "_fill_allowed_controls",
        lambda *_args: pytest.fail("CAPTCHA must stop before filling"),
    )
    monkeypatch.setattr(
        runner,
        "_emit",
        lambda status, _message, **_details: emitted.append(status),
    )

    assert runner.run_headed_autofill(_actual_plan(resume_path)) == 3
    assert emitted == ["blocked"]


def test_multistep_watcher_follows_exact_continuation_then_prefills(
    monkeypatch: pytest.MonkeyPatch, resume_path: Path
):
    form_snapshot = {
        "page_text": "填写简历",
        "frame_urls": [],
        "controls": [
            {
                "dom_index": 0,
                "tag": "input",
                "type": "text",
                "label": "姓名",
                "visible": True,
            }
        ],
    }

    class MultistepPage:
        url = "https://campus-talent.alibaba.com/campus/apply"

        def __init__(self) -> None:
            self.closed = False

        def is_closed(self) -> bool:
            return self.closed

        def wait_for_timeout(self, _milliseconds: int) -> None:
            self.closed = True

    page = MultistepPage()
    navigations: list[object] = []
    prefills: list[object] = []
    monkeypatch.setattr(
        runner,
        "_wait_for_human_verification",
        lambda _page, snapshot: (dict(snapshot), ()),
    )

    def navigate(_page: object, snapshot: object):
        navigations.append(snapshot)
        return page, form_snapshot, True

    monkeypatch.setattr(runner, "_navigate_to_application_form", navigate)
    monkeypatch.setattr(
        runner,
        "_prefill_current_page",
        lambda _page, snapshot, _plan, previous_signature=None: (
            prefills.append(snapshot) or ("filled",)
        ),
    )
    monkeypatch.setattr(runner, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_collect_snapshot", lambda _page: form_snapshot)

    runner._continue_prefilling_visible_steps(
        page,
        {"page_text": "开始投递", "frame_urls": [], "controls": []},
        _actual_plan(resume_path),
    )

    assert len(navigations) == 1
    assert prefills == [form_snapshot]


def test_runner_only_clicks_the_preclassified_apply_navigation_target():
    source = inspect.getsource(runner)
    navigation_source = inspect.getsource(runner._navigate_to_application_form)
    assert source.count(".click(") == 1
    assert "locator(selector).click(" in navigation_source
    assert "data-ai-job-agent-apply-target" in source
    assert "type=submit" not in navigation_source.casefold()
    assert "submit application" not in navigation_source.casefold()
    assert ".press(" not in source
    assert ".check(" not in source
    assert ".submit(" not in source


def test_subprocess_receives_private_values_only_over_stdin(
    monkeypatch: pytest.MonkeyPatch, resume_path: Path
):
    plan = create_autofill_plan(
        "https://boards.greenhouse.io/acme/jobs/1",
        PROFILE,
        resume_path,
        consent=True,
        dry_run=False,
    )
    captured: dict[str, object] = {}

    class CapturingInput(io.StringIO):
        def close(self) -> None:
            captured["stdin"] = self.getvalue()
            super().close()

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = CapturingInput()
            self.killed = False

        def kill(self) -> None:
            self.killed = True

    fake_process = FakeProcess()

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return fake_process

    monkeypatch.setattr("job_assistant.autofill_runner.subprocess.Popen", fake_popen)
    returned = start_autofill_process(plan, python_executable="python-safe")

    assert returned is fake_process
    command = captured["command"]
    assert command == ["python-safe", "-m", "job_assistant.autofill_runner"]
    assert PROFILE["email"] not in " ".join(command)
    assert PROFILE["phone"] not in " ".join(command)
    kwargs = captured["kwargs"]
    assert kwargs["shell"] is False
    assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    assert kwargs["env"]["PYTHONUTF8"] == "1"
    private_payload = json.loads(str(captured["stdin"]))
    assert private_payload["profile"]["email"] == PROFILE["email"]
    assert private_payload["profile"].get("national_id") is None
    assert private_payload["consent"] is True
    assert private_payload["dry_run"] is False
