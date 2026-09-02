from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class FormControl:
    """A deliberately small, serialisable view of one form control."""

    selector: str = field(repr=False)
    tag: str = "input"
    input_type: str = "text"
    label: str = field(default="", repr=False)
    placeholder: str = field(default="", repr=False)
    name: str = field(default="", repr=False)
    element_id: str = field(default="", repr=False)
    aria_label: str = field(default="", repr=False)
    aria_description: str = field(default="", repr=False)
    nearby_text: str = field(default="", repr=False)
    autocomplete: str = ""
    required: bool = False
    visible: bool = True
    disabled: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FormControl":
        selector = str(value.get("selector") or value.get("css") or "").strip()
        if not selector:
            fallback = str(value.get("id") or value.get("name") or "control").strip()
            selector = f"#{fallback}" if value.get("id") else f"[name='{fallback}']"
        return cls(
            selector=selector,
            tag=str(value.get("tag") or "input").casefold(),
            input_type=str(value.get("input_type") or value.get("type") or "text").casefold(),
            label=str(value.get("label") or ""),
            placeholder=str(value.get("placeholder") or ""),
            name=str(value.get("name") or ""),
            element_id=str(value.get("element_id") or value.get("id") or ""),
            aria_label=str(value.get("aria_label") or value.get("aria-label") or ""),
            aria_description=str(
                value.get("aria_description")
                or value.get("aria-description")
                or value.get("aria_describedby_text")
                or ""
            ),
            nearby_text=str(value.get("nearby_text") or value.get("context") or ""),
            autocomplete=str(value.get("autocomplete") or "").casefold(),
            required=any(
                value.get(key) is True
                for key in ("required", "aria_required", "label_required")
            ),
            visible=value.get("visible") is not False,
            disabled=value.get("disabled") is True,
        )


@dataclass(frozen=True, slots=True)
class PageSnapshot:
    page_url: str = field(repr=False)
    controls: tuple[FormControl, ...] = ()
    page_text: str = field(default="", repr=False)
    frame_urls: tuple[str, ...] = field(default=(), repr=False)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PageSnapshot":
        controls: list[FormControl] = []
        for item in value.get("controls", ()):
            if isinstance(item, FormControl):
                controls.append(item)
            elif isinstance(item, Mapping):
                controls.append(FormControl.from_mapping(item))
        raw_frames = value.get("frame_urls", ())
        if isinstance(raw_frames, (str, bytes)):
            raw_frames = (raw_frames,)
        return cls(
            page_url=str(value.get("page_url") or value.get("url") or ""),
            controls=tuple(controls),
            page_text=str(value.get("page_text") or ""),
            frame_urls=tuple(str(item) for item in raw_frames),
        )


@dataclass(frozen=True, slots=True)
class FieldMatch:
    selector: str = field(repr=False)
    canonical_field: str | None
    confidence: float
    evidence_sources: tuple[str, ...]
    sensitive: bool
    needs_user_confirmation: bool
    required: bool
    reason: str


@dataclass(frozen=True, slots=True)
class PageBlocker:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ConfirmationItem:
    selector: str = field(repr=False)
    field_name: str
    reason: str
    sensitive: bool = False
    prompt: str = field(default="", repr=False)

    def public_summary(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "reason": self.reason,
            "sensitive": self.sensitive,
            "has_prompt": bool(self.prompt),
        }


@dataclass(frozen=True, slots=True)
class FillAction:
    selector: str = field(repr=False)
    field_name: str
    value: str = field(repr=False)
    input_type: str = "text"
    confidence: float = 1.0

    def public_summary(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "input_type": self.input_type,
            "confidence": round(self.confidence, 3),
            "has_value": bool(self.value),
        }


@dataclass(frozen=True, slots=True)
class FormFillPlan:
    page_url: str = field(repr=False)
    actions: tuple[FillAction, ...]
    needs_user_confirmation: tuple[ConfirmationItem, ...]
    blockers: tuple[PageBlocker, ...]
    matches: tuple[FieldMatch, ...]

    @property
    def can_fill(self) -> bool:
        return bool(self.actions) and not self.blockers

    def public_summary(self) -> dict[str, Any]:
        # Values and raw control text are intentionally excluded.
        return {
            "fields_to_fill": [action.public_summary() for action in self.actions],
            "needs_user_confirmation": [
                item.public_summary() for item in self.needs_user_confirmation
            ],
            "blockers": [blocker.code for blocker in self.blockers],
            "can_fill": self.can_fill,
            "will_submit": False,
        }


def coerce_snapshot(value: PageSnapshot | Mapping[str, Any]) -> PageSnapshot:
    if isinstance(value, PageSnapshot):
        return value
    if isinstance(value, Mapping):
        return PageSnapshot.from_mapping(value)
    raise TypeError("snapshot must be PageSnapshot or a mapping")


def coerce_controls(
    value: Sequence[FormControl | Mapping[str, Any]],
) -> tuple[FormControl, ...]:
    controls: list[FormControl] = []
    for item in value:
        if isinstance(item, FormControl):
            controls.append(item)
        elif isinstance(item, Mapping):
            controls.append(FormControl.from_mapping(item))
        else:
            raise TypeError("controls must contain FormControl or mappings")
    return tuple(controls)


__all__ = [
    "ConfirmationItem",
    "FieldMatch",
    "FillAction",
    "FormControl",
    "FormFillPlan",
    "PageBlocker",
    "PageSnapshot",
    "coerce_controls",
    "coerce_snapshot",
]
