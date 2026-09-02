from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .autofill import (
    AutofillError,
    AutofillPlan,
    validate_application_url,
)


MAX_STDIN_BYTES = 64 * 1024
CONTROL_SELECTOR = "input, select, textarea"
_TEXTAREA_ONLY_FIELDS = frozenset(
    {
        "education",
        "certifications",
        "internships",
        "work_experience",
        "projects",
        "awards",
        "self_introduction",
    }
)
_BROWSER_STATE_ROOT = Path(__file__).resolve().parents[1] / "data" / "browser_profiles"

_APPLY_NAVIGATION_TEXT = frozenset(
    {
        "apply",
        "apply now",
        "apply for this job",
        "start application",
        "申请",
        "立即申请",
        "申请职位",
        "开始申请",
        "投递",
        "立即投递",
        "加入意向单",
        "开始投递",
    }
)
_FINAL_SUBMIT_TEXT = frozenset(
    {
        "submit",
        "submit application",
        "send application",
        "提交",
        "提交申请",
        "确认提交",
    }
)


@dataclass(frozen=True, slots=True)
class FormBlocker:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class ApplyNavigationTarget:
    """One high-confidence Apply control; never a final Submit control."""

    dom_index: int
    tag: str
    href: str
    confidence: float


def choose_apply_navigation_target(
    controls: Sequence[Mapping[str, Any]],
) -> ApplyNavigationTarget | None:
    """Choose a single exact Apply control from a job-detail page.

    Fuzzy text is deliberately rejected because clicking the wrong button is
    more harmful than asking the applicant to take over.  Duplicated responsive
    links to the same destination are collapsed.
    """

    ranked: list[ApplyNavigationTarget] = []
    seen_links: set[str] = set()
    for control in controls:
        if control.get("visible") is False:
            continue
        raw_text = " ".join(
            str(control.get(key) or "")
            for key in ("text", "aria_label", "title")
        )
        text = " ".join(raw_text.casefold().split())
        if not text or text in _FINAL_SUBMIT_TEXT or text not in _APPLY_NAVIGATION_TEXT:
            continue
        try:
            dom_index = int(control.get("dom_index"))
        except (TypeError, ValueError):
            continue
        tag = str(control.get("tag") or "").casefold()
        if tag not in {"a", "button"} or dom_index < 0:
            continue
        href = str(control.get("href") or "").strip()
        if href:
            if href in seen_links:
                continue
            seen_links.add(href)
        confidence = 0.99 if text in {"apply now", "立即申请", "申请职位", "立即投递"} else 0.97
        if href and any(marker in href.casefold() for marker in ("apply", "application")):
            confidence = 1.0
        ranked.append(ApplyNavigationTarget(dom_index, tag, href, confidence))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item.confidence, bool(item.href)), reverse=True)
    return ranked[0]


def _normalized_hint(control: Mapping[str, Any]) -> str:
    parts = (
        control.get("name", ""),
        control.get("id", ""),
        control.get("label", ""),
        control.get("aria_label", ""),
        control.get("nearby_text", ""),
        control.get("placeholder", ""),
        control.get("autocomplete", ""),
    )
    return " ".join(str(part).casefold() for part in parts if part)


def _normalized_label(control: Mapping[str, Any]) -> str:
    """Return one bounded field label with required/optional markers removed."""

    raw = str(
        control.get("label")
        or control.get("aria_label")
        or control.get("nearby_text")
        or ""
    ).casefold()
    compact = "".join(character for character in raw if character.isalnum())
    for marker in ("required", "optional", "必填", "选填"):
        compact = compact.removesuffix(marker)
    return compact


def canonical_field(control: Mapping[str, Any]) -> str | None:
    """Map a DOM control to one explicitly allow-listed candidate fact."""

    input_type = str(control.get("type", "")).casefold()
    autocomplete = str(control.get("autocomplete", "")).casefold()
    hint = _normalized_hint(control)
    identifiers = {
        "".join(
            character
            for character in str(control.get(key, "")).casefold()
            if character.isalnum()
        )
        for key in ("name", "id")
    }
    identifiers.discard("")
    label = _normalized_label(control)

    if input_type == "file":
        if "resume" in hint or "résumé" in hint or "cv" in hint or "简历" in hint:
            return "resume"
        return None
    if autocomplete == "name" or identifiers.intersection(
        {
            "fullname",
            "legalname",
            "candidatename",
            "applicantname",
            "jobapplicationname",
        }
    ) or label in {"fullname", "legalname", "name", "姓名", "真实姓名"}:
        return "full_name"
    if autocomplete == "email" or identifiers.intersection(
        {"email", "jobapplicationemail", "applicationemail", "candidateemail"}
    ) or label in {"email", "emailaddress", "邮箱", "电子邮箱"}:
        return "email"
    if autocomplete == "tel" or identifiers.intersection(
        {
            "phone",
            "phonenumber",
            "mobile",
            "jobapplicationphone",
            "jobapplicationphonenumber",
            "applicationphone",
            "candidatephone",
        }
    ) or label in {
        "phone",
        "phonenumber",
        "mobile",
        "mobilephone",
        "手机号",
        "手机号码",
        "联系电话",
        "电话",
    }:
        return "phone"
    if autocomplete == "given-name" or identifiers.intersection(
        {
            "firstname",
            "givenname",
            "jobapplicationfirstname",
            "applicationfirstname",
            "candidatefirstname",
        }
    ) or label in {"firstname", "givenname", "名"}:
        return "first_name"
    if (
        autocomplete == "family-name"
        or identifiers.intersection(
            {
                "lastname",
                "familyname",
                "surname",
                "jobapplicationlastname",
                "applicationlastname",
                "candidatelastname",
            }
        )
        or label in {"lastname", "familyname", "surname", "姓", "姓氏"}
    ):
        return "last_name"
    if (
        autocomplete in {"address-level2", "address-level1"}
        or identifiers.intersection(
            {
                "location",
                "currentlocation",
                "city",
                "jobapplicationlocation",
                "applicationlocation",
                "candidatelocation",
            }
        )
        or label in {
            "location",
            "currentlocation",
            "city",
            "所在地",
            "现居地",
            "当前城市",
            "城市",
        }
    ):
        return "location"
    if identifiers.intersection(
        {
            "school",
            "schoolname",
            "university",
            "universityname",
            "college",
            "educationalschool",
        }
    ) or label in {
        "school",
        "schoolname",
        "university",
        "universityname",
        "college",
        "学校",
        "院校",
        "毕业院校",
    }:
        return "school"
    if identifiers.intersection(
        {"degree", "highestdegree", "educationlevel", "academicdegree"}
    ) or label in {
        "degree",
        "highestdegree",
        "educationlevel",
        "学历",
        "学位",
        "最高学历",
    }:
        return "degree"
    if identifiers.intersection(
        {"major", "fieldofstudy", "specialization", "academicmajor"}
    ) or label in {
        "major",
        "fieldofstudy",
        "specialization",
        "专业",
        "所学专业",
    }:
        return "major"
    if identifiers.intersection(
        {
            "graduationdate",
            "graduationmonth",
            "expectedgraduationdate",
            "expectedgraduation",
        }
    ) or label in {
        "graduationdate",
        "graduationmonth",
        "expectedgraduationdate",
        "毕业时间",
        "毕业日期",
        "预计毕业时间",
    }:
        return "graduation_date"
    if identifiers.intersection(
        {"education", "educationhistory", "educationbackground", "academicbackground"}
    ) or label in {"education", "educationhistory", "教育经历", "教育背景"}:
        return "education"
    if identifiers.intersection(
        {"skills", "skillset", "professionalskills", "technicalskills"}
    ) or label in {"skills", "professionalskills", "专业技能", "技能"}:
        return "skills"
    if identifiers.intersection(
        {"languages", "languageskills", "languageproficiency"}
    ) or label in {"languages", "languageskills", "语言能力", "语言"}:
        return "languages"
    if identifiers.intersection(
        {"certifications", "certificates", "certification"}
    ) or label in {"certifications", "certificates", "证书", "资格证书", "认证"}:
        return "certifications"
    if identifiers.intersection(
        {"internships", "internshipexperience", "internshipexperiences"}
    ) or label in {"internships", "internshipexperience", "实习经历", "实习经验"}:
        return "internships"
    if identifiers.intersection(
        {"workexperience", "employmenthistory", "professionalexperience"}
    ) or label in {"workexperience", "employmenthistory", "工作经历", "工作经验"}:
        return "work_experience"
    if identifiers.intersection(
        {"projects", "projectexperience", "projectexperiences"}
    ) or label in {"projects", "projectexperience", "项目经历", "项目经验"}:
        return "projects"
    if identifiers.intersection({"awards", "honors", "honours"}) or label in {
        "awards",
        "honors",
        "honours",
        "奖项",
        "荣誉",
        "获奖经历",
    }:
        return "awards"
    if identifiers.intersection(
        {"selfintroduction", "personalsummary", "professionalsummary", "aboutme"}
    ) or label in {
        "selfintroduction",
        "personalsummary",
        "自我介绍",
        "个人概述",
        "个人总结",
    }:
        return "self_introduction"
    return None


def _is_required(control: Mapping[str, Any]) -> bool:
    return any(
        control.get(key) is True
        for key in (
            "required",
            "aria_required",
            "required_ancestor",
            "label_required",
        )
    )


def detect_form_blockers(snapshot: Mapping[str, Any]) -> tuple[FormBlocker, ...]:
    """Detect stop conditions without exposing page or applicant content."""

    blockers: list[FormBlocker] = []
    page_text = str(snapshot.get("page_text", "")).casefold()
    frame_urls = tuple(str(value).casefold() for value in snapshot.get("frame_urls", ()))
    captcha_phrases = (
        "verify you are human",
        "complete the captcha",
        "security verification",
        "请完成验证码",
        "验证您是人类",
    )
    captcha_frame = any(
        marker in frame_url
        for frame_url in frame_urls
        for marker in ("recaptcha", "hcaptcha", "challenges.cloudflare")
    )
    if captcha_frame or any(phrase in page_text for phrase in captcha_phrases):
        blockers.append(FormBlocker("captcha", "页面需要人工完成 CAPTCHA/安全验证"))

    controls = tuple(
        control
        for control in snapshot.get("controls", ())
        if isinstance(control, Mapping)
    )
    if any(
        str(control.get("type", "")).casefold() == "password"
        and control.get("visible") is not False
        for control in controls
    ) or any(
        phrase in page_text
        for phrase in (
            "log in to apply",
            "login to apply",
            "sign in to apply",
            "please log in to continue",
            "please sign in to continue",
            "请先登录再申请",
            "请登录后继续",
        )
    ):
        blockers.append(FormBlocker("login_required", "页面要求先登录，已停止自动填写"))

    seen_labels: set[str] = set()
    for control in controls:
        input_type = str(control.get("type", "")).casefold()
        if input_type in {"hidden", "submit", "button", "reset", "image"}:
            continue
        if control.get("visible") is False and input_type != "file":
            continue
        if not _is_required(control) or canonical_field(control) is not None:
            continue
        raw_label = str(
            control.get("label")
            or control.get("name")
            or control.get("id")
            or "未命名必填题"
        ).strip()
        label = " ".join(raw_label.split())[:80] or "未命名必填题"
        if label in seen_labels:
            continue
        seen_labels.add(label)
        blockers.append(
            FormBlocker(
                "unsupported_required_question",
                f"发现需要人工回答的必填项：{label}",
            )
        )
    return tuple(blockers)


_SNAPSHOT_SCRIPT = r"""
() => {
  const visible = (element) => {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' &&
      rect.width > 0 && rect.height > 0;
  };
  const labelText = (element) => {
    if (element.labels && element.labels.length) {
      return Array.from(element.labels).map((label) => label.innerText || label.textContent || '').join(' ');
    }
    const ancestor = element.closest('label');
    if (ancestor) return ancestor.innerText || ancestor.textContent || '';
    const id = element.id;
    if (id) {
      const explicit = Array.from(document.querySelectorAll('label')).find(
        (label) => label.htmlFor === id
      );
      if (explicit) return explicit.innerText || explicit.textContent || '';
    }
    const labelledBy = String(element.getAttribute('aria-labelledby') || '')
      .split(/\s+/).filter(Boolean)
      .map((labelId) => document.getElementById(labelId))
      .filter(Boolean)
      .map((labelElement) => labelElement.innerText || labelElement.textContent || '')
      .join(' ');
    if (labelledBy) return labelledBy;
    const formItem = element.closest(
      '.next-form-item, .ant-form-item, .arco-form-item, .semi-form-field, ' +
      '[data-testid*="form-field"], [class*="FormItem"], [class*="form-item"]'
    );
    if (formItem) {
      const nearbyLabel = formItem.querySelector(
        '.next-form-item-label, .ant-form-item-label, .arco-form-label-item, ' +
        '.semi-form-field-label, label, [class*="field-label"], [class*="FieldLabel"]'
      );
      if (nearbyLabel) return nearbyLabel.innerText || nearbyLabel.textContent || '';
    }
    return '';
  };
  const controls = Array.from(document.querySelectorAll('input, select, textarea')).map(
    (element, domIndex) => {
      const requiredAncestor = element.closest(
        '.required, [data-required="true"], [aria-required="true"]'
      );
      const label = labelText(element).slice(0, 200);
      return {
        dom_index: domIndex,
        tag: element.tagName.toLowerCase(),
        type: (element.getAttribute('type') || '').toLowerCase(),
        name: element.getAttribute('name') || '',
        id: element.id || '',
        label,
        aria_label: element.getAttribute('aria-label') || '',
        nearby_text: label,
        placeholder: element.getAttribute('placeholder') || '',
        autocomplete: (element.getAttribute('autocomplete') || '').toLowerCase(),
        required: Boolean(element.required),
        aria_required: element.getAttribute('aria-required') === 'true',
        required_ancestor: Boolean(requiredAncestor),
        label_required: /(^|\s)\*\s*$|\(required\)/i.test(label),
        visible: visible(element)
      };
    }
  );
  return {
    page_text: (document.body && document.body.innerText || '').slice(0, 30000),
    controls,
    frame_urls: Array.from(document.querySelectorAll('iframe'))
      .filter(visible)
      .map((frame) => frame.src || '')
      .filter(Boolean)
  };
}
"""

_APPLY_CONTROLS_SCRIPT = r"""
() => {
  const attribute = 'data-ai-job-agent-apply-target';
  const visible = (element) => {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' &&
      rect.width > 0 && rect.height > 0;
  };
  return Array.from(document.querySelectorAll('a[href], button, [role="button"]'))
    .map((element, domIndex) => {
      element.setAttribute(attribute, String(domIndex));
      return {
        dom_index: domIndex,
        tag: element.tagName.toLowerCase() === 'a' ? 'a' : 'button',
        href: element.tagName.toLowerCase() === 'a' ? String(element.href || '') : '',
        text: String(element.innerText || element.textContent || '').trim().slice(0, 160),
        aria_label: String(element.getAttribute('aria-label') || '').slice(0, 160),
        title: String(element.getAttribute('title') || '').slice(0, 160),
        visible: visible(element)
      };
    });
}
"""


def _collect_snapshot(page: Any) -> dict[str, Any]:
    result = page.evaluate(_SNAPSHOT_SCRIPT)
    if not isinstance(result, dict):
        raise AutofillError("无法识别当前申请表单")
    return result


def _snapshot_has_application_fields(snapshot: Mapping[str, Any]) -> bool:
    return any(
        isinstance(control, Mapping)
        and control.get("visible") is not False
        and canonical_field(control) is not None
        for control in snapshot.get("controls", ())
    )


def _navigate_to_application_form(
    page: Any, snapshot: Mapping[str, Any]
) -> tuple[Any, dict[str, Any], bool]:
    """Follow one exact Apply control when the selected URL is a job page."""

    if _snapshot_has_application_fields(snapshot):
        return page, dict(snapshot), False
    raw_controls = page.evaluate(_APPLY_CONTROLS_SCRIPT)
    if not isinstance(raw_controls, list):
        return page, dict(snapshot), False
    target = choose_apply_navigation_target(
        tuple(item for item in raw_controls if isinstance(item, Mapping))
    )
    if target is None:
        return page, dict(snapshot), False

    if target.href:
        destination = validate_application_url(target.href)
        page.goto(destination, wait_until="domcontentloaded", timeout=45_000)
    else:
        selector = f'[data-ai-job-agent-apply-target="{target.dom_index}"]'
        context = page.context
        pages_before = tuple(context.pages)
        try:
            page.locator(selector).click(timeout=10_000)
        except Exception:
            # Some Apply controls close the detail tab while opening the form.
            # Continue only when a replacement page actually appeared.
            if not _page_is_closed(page):
                raise
        if not _page_is_closed(page):
            page.wait_for_timeout(750)
        new_pages = [candidate for candidate in context.pages if candidate not in pages_before]
        if new_pages:
            page = new_pages[-1]
        elif _page_is_closed(page):
            raise AutofillError("申请页窗口已关闭，未发现新的官网表单")
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            # Client-rendered forms may never cause a navigation event.
            pass
    validate_application_url(page.url)
    return page, _collect_snapshot(page), True


def _wait_for_human_verification(
    page: Any, snapshot: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, tuple[FormBlocker, ...]]:
    """Keep the visible browser open until login/CAPTCHA is cleared or closed."""

    current = dict(snapshot)
    hard = hard_stop_blockers(detect_form_blockers(current))
    if not hard:
        return current, ()
    _emit(
        "blocked",
        "需要你在可见浏览器中完成登录或验证；完成后程序会从当前页面继续",
        blockers=[blocker.to_dict() for blocker in hard],
    )
    try:
        page.bring_to_front()
    except Exception:
        if _page_is_closed(page):
            return None, hard
        raise
    while not _page_is_closed(page):
        try:
            page.wait_for_timeout(1_000)
        except Exception:
            if _page_is_closed(page):
                return None, hard
            raise
        try:
            validate_application_url(page.url)
            current = _collect_snapshot(page)
        except AutofillError:
            raise
        except Exception:
            if _page_is_closed(page):
                return None, hard
            continue
        hard = hard_stop_blockers(detect_form_blockers(current))
        if not hard:
            _emit("resuming", "验证已完成，继续识别申请表单")
            return current, ()
    return None, hard


def _fill_allowed_controls(
    page: Any, snapshot: Mapping[str, Any], plan: AutofillPlan
) -> tuple[str, ...]:
    values = plan.fields
    filled: list[str] = []
    used: set[str] = set()
    all_controls = page.locator(CONTROL_SELECTOR)

    for original in snapshot.get("controls", ()):
        if not isinstance(original, Mapping):
            continue
        field_name = canonical_field(original)
        if field_name is None or field_name in used:
            continue
        input_type = str(original.get("type", "")).casefold()
        if original.get("visible") is False and input_type != "file":
            continue
        dom_index = original.get("dom_index")
        if not isinstance(dom_index, int) or dom_index < 0:
            continue
        locator = all_controls.nth(dom_index)
        try:
            # Re-read a small set of non-sensitive attributes immediately before
            # writing, so a dynamically replaced form cannot redirect a value into
            # an unrelated field between inspection and filling.
            live = locator.evaluate(
                """(element) => ({
                  type: (element.getAttribute('type') || '').toLowerCase(),
                  name: element.getAttribute('name') || '',
                  id: element.id || '',
                  placeholder: element.getAttribute('placeholder') || '',
                  autocomplete: (element.getAttribute('autocomplete') || '').toLowerCase(),
                  tag: (element.tagName || '').toLowerCase(),
                  value: element.value || '',
                  aria_label: element.getAttribute('aria-label') || '',
                  label: (() => {
                    if (element.labels && element.labels.length) {
                      return Array.from(element.labels)
                        .map((item) => item.textContent || '').join(' ');
                    }
                    const ancestor = element.closest('label');
                    if (ancestor) return ancestor.textContent || '';
                    const labelledBy = String(element.getAttribute('aria-labelledby') || '')
                      .split(/\\s+/).filter(Boolean)
                      .map((labelId) => document.getElementById(labelId))
                      .filter(Boolean)
                      .map((item) => item.textContent || '').join(' ');
                    if (labelledBy) return labelledBy;
                    const formItem = element.closest(
                      '.next-form-item, .ant-form-item, .arco-form-item, ' +
                      '.semi-form-field, [data-testid*="form-field"], ' +
                      '[class*="FormItem"], [class*="form-item"]'
                    );
                    const nearby = formItem && formItem.querySelector(
                      '.next-form-item-label, .ant-form-item-label, ' +
                      '.arco-form-label-item, .semi-form-field-label, label, ' +
                      '[class*="field-label"], [class*="FieldLabel"]'
                    );
                    return nearby ? (nearby.textContent || '') : '';
                  })()
                })"""
            )
            if isinstance(live, dict) and live.get("label"):
                live["nearby_text"] = live["label"]
            if not isinstance(live, dict) or canonical_field(live) != field_name:
                continue
            if field_name != "resume" and field_name not in values:
                continue
            tag_name = str(live.get("tag") or original.get("tag", "")).casefold()
            current_value = str(live.get("value") or "").strip()
            if current_value:
                # Respect values entered by the applicant, the browser or the ATS.
                used.add(field_name)
                filled.append(field_name)
                continue
            if field_name == "resume":
                locator.set_input_files(str(plan.resume_path))
            else:
                if field_name in _TEXTAREA_ONLY_FIELDS and tag_name != "textarea":
                    continue
                target_value = values[field_name]
                if tag_name == "select":
                    # Select only an exact visible label; fuzzy choice could change
                    # a legal or preference declaration.
                    locator.select_option(label=target_value)
                elif tag_name in {"input", "textarea"}:
                    if input_type == "month":
                        month_match = re.fullmatch(
                            r"(\d{4})[./-](\d{1,2})", target_value
                        )
                        if not month_match:
                            continue
                        target_value = f"{month_match.group(1)}-{int(month_match.group(2)):02d}"
                    elif input_type == "date" and not re.fullmatch(
                        r"\d{4}-\d{2}-\d{2}", target_value
                    ):
                        continue
                    locator.fill(target_value)
                else:
                    continue
            used.add(field_name)
            filled.append(field_name)
        except Exception:
            # One unsupported control must not make the runner try broader or less
            # precise selectors. Missing required controls are reported below.
            continue
    return tuple(filled)


def _missing_required_supported_fields(
    snapshot: Mapping[str, Any], filled_fields: Sequence[str]
) -> tuple[str, ...]:
    filled = set(filled_fields)
    missing: list[str] = []
    for control in snapshot.get("controls", ()):
        if not isinstance(control, Mapping) or not _is_required(control):
            continue
        field_name = canonical_field(control)
        if field_name is not None and field_name not in filled and field_name not in missing:
            missing.append(field_name)
    return tuple(missing)


def hard_stop_blockers(blockers: Sequence[FormBlocker]) -> tuple[FormBlocker, ...]:
    """Return blockers that prohibit entering any applicant data."""

    hard_codes = {"captcha", "login_required"}
    return tuple(blocker for blocker in blockers if blocker.code in hard_codes)


def _manual_takeover_blockers(
    blockers: Sequence[FormBlocker], missing_fields: Sequence[str], *, found_fields: bool
) -> tuple[FormBlocker, ...]:
    manual = [
        blocker
        for blocker in blockers
        if blocker.code not in {"captcha", "login_required"}
    ]
    for field_name in missing_fields:
        manual.append(
            FormBlocker(
                "standard_field_needs_manual_input",
                f"标准必填字段需要人工填写：{field_name}",
            )
        )
    if not found_fields:
        manual.append(
            FormBlocker(
                "form_needs_manual_input",
                "未找到可安全自动填写的标准字段，请人工接管",
            )
        )
    return tuple(manual)


def _prefill_current_page(
    page: Any,
    snapshot: Mapping[str, Any],
    plan: AutofillPlan,
    *,
    previous_signature: tuple[Any, ...] | None = None,
) -> tuple[Any, ...]:
    """Fill every currently visible known field and emit only changed review state."""

    blockers = detect_form_blockers(snapshot)
    filled = _fill_allowed_controls(page, snapshot, plan)
    missing = _missing_required_supported_fields(snapshot, filled)
    manual_blockers = _manual_takeover_blockers(
        blockers, missing, found_fields=bool(filled)
    )
    signature: tuple[Any, ...] = (
        str(getattr(page, "url", "")),
        tuple(sorted(filled)),
        tuple((blocker.code, blocker.message) for blocker in manual_blockers),
    )
    if signature == previous_signature:
        return signature
    if manual_blockers:
        _emit(
            "manual_action_required",
            "已填写可安全识别的资料；请在可见浏览器中人工完成其余项目、核对并手动提交",
            filled_fields=list(filled),
            blockers=[blocker.to_dict() for blocker in manual_blockers],
        )
    else:
        _emit(
            "ready_for_review",
            "已填写允许的资料，请在可见浏览器中核对并手动提交",
            filled_fields=list(filled),
        )
    return signature


def _continue_prefilling_visible_steps(
    page: Any,
    snapshot: Mapping[str, Any],
    plan: AutofillPlan,
) -> None:
    """Keep watching a multi-step SPA and fill known fields as each step appears."""

    current = dict(snapshot)
    signature: tuple[Any, ...] | None = None
    navigation_count = 0
    first_pass = True
    while first_pass or not _page_is_closed(page):
        first_pass = False
        current, _ = _wait_for_human_verification(page, current)
        if current is None:
            return

        # Application systems such as Alibaba use several safe transition
        # screens (job detail -> intention -> start application -> login ->
        # form). Follow only exact, pre-classified application verbs; final
        # Submit text remains permanently excluded.
        if not _snapshot_has_application_fields(current) and navigation_count < 6:
            page, navigated_snapshot, navigated = _navigate_to_application_form(
                page, current
            )
            if navigated:
                navigation_count += 1
                current = navigated_snapshot
                _emit("application_form_opened", "已继续进入该岗位的申请流程")
                continue

        signature = _prefill_current_page(
            page,
            current,
            plan,
            previous_signature=signature,
        )
        if _page_is_closed(page):
            return
        try:
            page.wait_for_timeout(750)
        except Exception:
            if _page_is_closed(page):
                return
            raise
        try:
            validate_application_url(page.url)
            current = _collect_snapshot(page)
        except AutofillError:
            raise
        except Exception:
            if _page_is_closed(page):
                return
            continue


def _emit(status: str, message: str, **details: Any) -> None:
    payload = {"status": status, "message": message, "will_submit": False, **details}
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def _safe_route(route: Any, request: Any) -> None:
    url = str(request.url)
    if url.startswith(("data:", "blob:", "about:")):
        route.continue_()
        return
    try:
        validate_application_url(url)
    except AutofillError:
        route.abort()
    else:
        route.continue_()


def _launch_visible_browser(playwright: Any) -> tuple[Any, str]:
    """Launch bundled Chromium, with a Windows Edge fallback.

    Some managed Windows installations reject Playwright's bundled executable
    with a side-by-side/spawn error even though the system browser is healthy.
    Edge uses the same Playwright Chromium protocol and preserves every safety
    restriction in this runner.
    """

    try:
        return playwright.chromium.launch(headless=False), "playwright-chromium"
    except Exception as bundled_error:
        if os.name != "nt":
            raise
        try:
            return (
                playwright.chromium.launch(headless=False, channel="msedge"),
                "system-edge",
            )
        except Exception as edge_error:
            raise RuntimeError(
                "Playwright Chromium 与系统 Microsoft Edge 均无法启动"
            ) from edge_error


def _browser_state_path(application_url: str) -> Path:
    """Return an opaque per-career-site path for local login-session reuse."""

    parsed = urlsplit(validate_application_url(application_url))
    first_path = next((part for part in parsed.path.split("/") if part), "root")
    scope = f"{parsed.hostname or ''}/{first_path}".encode("utf-8")
    key = hashlib.sha256(scope).hexdigest()[:24]
    return _BROWSER_STATE_ROOT / key / "state.json"


def _save_browser_state(context: Any, path: Path) -> None:
    """Persist cookies/local storage locally without logging their contents."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(path))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        # Session reuse is a convenience and must never hide the application result.
        pass


def _page_is_closed(page: Any) -> bool:
    try:
        return bool(page.is_closed())
    except Exception:
        return True


def _wait_for_manual_review_window(page: Any) -> None:
    """Wait until the applicant closes the visible page without treating it as failure."""

    try:
        page.bring_to_front()
    except Exception:
        if _page_is_closed(page):
            return
        raise
    while not _page_is_closed(page):
        try:
            page.wait_for_timeout(500)
        except Exception:
            if _page_is_closed(page):
                return
            raise


def run_headed_autofill(plan: AutofillPlan) -> int:
    """Fill approved fields in a visible browser and wait for manual review.

    The runner may follow one exact ``Apply`` control when a selected URL is a
    job-detail page.  It deliberately contains no final-submit selector. Login
    and CAPTCHA pages pause in the visible browser; special questions stay
    untouched for the applicant to answer and review.
    """

    plan.require_execution_authorized()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _emit("error", "缺少 Playwright，请先安装项目依赖和 Chromium")
        return 2

    stage = "browser_launch"
    try:
        with sync_playwright() as playwright:
            browser, browser_engine = _launch_visible_browser(playwright)
            if browser_engine == "system-edge":
                _emit(
                    "browser_fallback",
                    "Playwright Chromium 不可用，已安全改用系统 Microsoft Edge",
                    browser_engine=browser_engine,
                )
            state_path = _browser_state_path(plan.application_url)
            context_options: dict[str, Any] = {}
            if state_path.is_file():
                context_options["storage_state"] = str(state_path)
            try:
                context = browser.new_context(**context_options)
            except Exception:
                if not context_options:
                    raise
                context = browser.new_context()
            context.route("**/*", _safe_route)
            page = context.new_page()
            try:
                stage = "open_job_page"
                page.goto(
                    plan.application_url,
                    wait_until="domcontentloaded",
                    timeout=45_000,
                )
                validate_application_url(page.url)
                stage = "read_job_page"
                snapshot = _collect_snapshot(page)
                stage = "wait_for_login_or_verification"
                snapshot, _ = _wait_for_human_verification(page, snapshot)
                if snapshot is None:
                    return 3
                stage = "open_application_form"
                # Authentication redirects often return to the original job page.
                # A second bounded navigation attempt lets the same visible session
                # re-enter Apply without ever clicking a final Submit control.
                for _navigation_attempt in range(2):
                    page, snapshot, navigated = _navigate_to_application_form(
                        page, snapshot
                    )
                    if navigated:
                        _emit("application_form_opened", "已进入该岗位的申请流程")
                    stage = "wait_for_application_verification"
                    snapshot, _ = _wait_for_human_verification(page, snapshot)
                    if snapshot is None:
                        return 3
                    if _snapshot_has_application_fields(snapshot) or not navigated:
                        break
                    stage = "open_application_form"
                stage = "fill_known_fields"
                stage = "wait_for_applicant_review"
                _continue_prefilling_visible_steps(page, snapshot, plan)
            finally:
                _save_browser_state(context, state_path)
                try:
                    context.close()
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass
    except AutofillError as exc:
        _emit("blocked", str(exc))
        return 3
    except Exception as exc:
        # Playwright exception text may contain page values. Keep the public
        # result generic and never dump a traceback or the private payload.
        _emit(
            "error",
            "官网填写进程遇到错误，未提交任何申请",
            error_stage=stage,
            error_code=type(exc).__name__[:80],
        )
        return 2
    return 0


def start_autofill_process(
    plan: AutofillPlan, *, python_executable: str | Path | None = None
) -> subprocess.Popen[str]:
    """Start the headed runner without putting personal data in argv or logs."""

    plan.require_execution_authorized()
    executable = str(python_executable or sys.executable)
    command = [executable, "-m", "job_assistant.autofill_runner"]
    creationflags = 0
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW
    process_env = os.environ.copy()
    # The monitor reads a strict UTF-8 JSONL event stream. Force the child pipe
    # to UTF-8 on Windows as well, otherwise Chinese blocker labels can become
    # mojibake even though no private field values are logged.
    process_env["PYTHONIOENCODING"] = "utf-8"
    process_env["PYTHONUTF8"] = "1"
    process = subprocess.Popen(
        command,
        cwd=str(Path(__file__).resolve().parents[1]),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        shell=False,
        creationflags=creationflags,
        env=process_env,
    )
    if process.stdin is None:
        process.kill()
        raise AutofillError("无法建立安全的本地填写通道")
    try:
        process.stdin.write(plan._private_json())
        process.stdin.close()
    except (BrokenPipeError, OSError) as exc:
        process.kill()
        raise AutofillError("无法启动官网填写进程") from exc
    return process


def _read_plan_from_stdin() -> AutofillPlan:
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        raise AutofillError("自动填写请求过大")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutofillError("自动填写请求格式无效") from exc
    if not isinstance(payload, Mapping):
        raise AutofillError("自动填写请求格式无效")
    plan = AutofillPlan._from_private_payload(payload)
    plan.require_execution_authorized()
    return plan


def main() -> int:
    try:
        plan = _read_plan_from_stdin()
    except AutofillError as exc:
        _emit("blocked", str(exc))
        return 3
    return run_headed_autofill(plan)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "FormBlocker",
    "ApplyNavigationTarget",
    "canonical_field",
    "choose_apply_navigation_target",
    "detect_form_blockers",
    "hard_stop_blockers",
    "run_headed_autofill",
    "start_autofill_process",
]
