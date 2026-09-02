from __future__ import annotations

import os
from collections.abc import Collection, Mapping
from dataclasses import replace

from .blockers import detect_page_blockers
from .field_detector import (
    ANSWER_GENERATOR_FIELDS,
    PUBLIC_AUTOFILL_FIELDS,
    FieldDetector,
)
from .models import (
    ConfirmationItem,
    FillAction,
    FormFillPlan,
    PageSnapshot,
    coerce_snapshot,
)


class FormPlanner:
    """Turn a read-only page snapshot into a non-executing fill plan."""

    def __init__(self, detector: FieldDetector | None = None) -> None:
        self.detector = detector or FieldDetector()

    def build_plan(
        self,
        snapshot: PageSnapshot | Mapping[str, object],
        applicant_values: Mapping[str, object],
    ) -> FormFillPlan:
        page = coerce_snapshot(snapshot)
        blockers = detect_page_blockers(page)
        matches = self.detector.detect_many(page.controls)
        control_by_selector = {control.selector: control for control in page.controls}
        actions: list[FillAction] = []
        confirmations: list[ConfirmationItem] = []

        for match in matches:
            field_name = match.canonical_field or "unknown"
            control = control_by_selector[match.selector]
            if match.needs_user_confirmation:
                prompt = ""
                if field_name in ANSWER_GENERATOR_FIELDS:
                    prompt = next(
                        (
                            value.strip()
                            for value in (
                                control.label,
                                control.aria_label,
                                control.aria_description,
                                control.nearby_text,
                            )
                            if value.strip()
                        ),
                        "",
                    )[:1_000]
                confirmations.append(
                    ConfirmationItem(
                        selector=match.selector,
                        field_name=field_name,
                        reason=match.reason,
                        sensitive=match.sensitive,
                        prompt=prompt,
                    )
                )
                continue
            if field_name not in PUBLIC_AUTOFILL_FIELDS:
                confirmations.append(
                    ConfirmationItem(
                        selector=match.selector,
                        field_name=field_name,
                        reason="field_not_allowlisted",
                        sensitive=match.sensitive,
                    )
                )
                continue
            raw_value = applicant_values.get(field_name)
            if isinstance(raw_value, str):
                value = raw_value.strip()
            elif field_name == "resume" and isinstance(raw_value, os.PathLike):
                value = os.fspath(raw_value).strip()
            elif raw_value is None:
                value = ""
            else:
                confirmations.append(
                    ConfirmationItem(
                        selector=match.selector,
                        field_name=field_name,
                        reason="structured_value_needs_rendering",
                    )
                )
                continue
            if not value:
                if match.required:
                    confirmations.append(
                        ConfirmationItem(
                            selector=match.selector,
                            field_name=field_name,
                            reason="required_value_missing",
                        )
                    )
                continue
            actions.append(
                FillAction(
                    selector=match.selector,
                    field_name=field_name,
                    value=value,
                    input_type=control.input_type,
                    confidence=match.confidence,
                )
            )

        # Never fill behind a CAPTCHA or login wall.  The proposed matches remain
        # visible in the review, but no executable actions are returned.
        if blockers:
            actions = []
        return FormFillPlan(
            page_url=page.page_url,
            actions=tuple(actions),
            needs_user_confirmation=tuple(confirmations),
            blockers=blockers,
            matches=matches,
        )

    def add_confirmed_generated_answers(
        self,
        plan: FormFillPlan,
        answers_by_selector: Mapping[str, str],
        *,
        confirmed_selectors: Collection[str],
        review_confirmed: bool,
    ) -> FormFillPlan:
        """Attach generator output only after the user reviewed each answer."""

        if review_confirmed is not True:
            raise ValueError("generated answers require explicit review confirmation")
        confirmed = {str(selector) for selector in confirmed_selectors}
        actions = list(plan.actions)
        remaining: list[ConfirmationItem] = []
        match_by_selector = {match.selector: match for match in plan.matches}
        for item in plan.needs_user_confirmation:
            answer = str(answers_by_selector.get(item.selector) or "").strip()
            if (
                item.reason == "answer_generator_required"
                and item.field_name in ANSWER_GENERATOR_FIELDS
                and item.selector in confirmed
                and answer
            ):
                match = match_by_selector[item.selector]
                actions.append(
                    FillAction(
                        selector=item.selector,
                        field_name=item.field_name,
                        value=answer,
                        input_type="text",
                        confidence=match.confidence,
                    )
                )
            else:
                remaining.append(item)
        return replace(
            plan,
            actions=tuple(actions),
            needs_user_confirmation=tuple(remaining),
        )


__all__ = ["FormPlanner"]
