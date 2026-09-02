from __future__ import annotations

from collections.abc import Mapping

from .models import PageBlocker, PageSnapshot, coerce_snapshot


CAPTCHA_TEXT_MARKERS = (
    "verify you are human",
    "complete the captcha",
    "security verification",
    "human verification",
    "请完成验证码",
    "请进行安全验证",
    "验证您是人类",
)
CAPTCHA_URL_MARKERS = (
    "recaptcha",
    "hcaptcha",
    "challenges.cloudflare.com",
    "turnstile",
    "/captcha",
)
LOGIN_TEXT_MARKERS = (
    "log in to apply",
    "login to apply",
    "sign in to apply",
    "please log in to continue",
    "please sign in to continue",
    "请登录后申请",
    "请先登录",
    "登录后继续",
)


def detect_page_blockers(
    snapshot: PageSnapshot | Mapping[str, object],
) -> tuple[PageBlocker, ...]:
    page = coerce_snapshot(snapshot)
    blockers: list[PageBlocker] = []
    text = page.page_text.casefold()
    frame_urls = tuple(url.casefold() for url in page.frame_urls)
    control_hints = " ".join(
        " ".join(
            (
                control.name,
                control.element_id,
                control.aria_label,
                control.label,
            )
        ).casefold()
        for control in page.controls
        if control.visible
    )

    if (
        any(marker in text for marker in CAPTCHA_TEXT_MARKERS)
        or any(marker in url for url in frame_urls for marker in CAPTCHA_URL_MARKERS)
        or any(marker in control_hints for marker in ("recaptcha", "hcaptcha", "captcha", "验证码"))
    ):
        blockers.append(PageBlocker("captcha", "CAPTCHA/安全验证需要用户本人完成"))

    password_visible = any(
        control.visible and not control.disabled and control.input_type == "password"
        for control in page.controls
    )
    if password_visible or any(marker in text for marker in LOGIN_TEXT_MARKERS):
        blockers.append(PageBlocker("login_required", "页面要求登录，需要用户本人完成"))

    return tuple(blockers)


__all__ = ["detect_page_blockers"]
