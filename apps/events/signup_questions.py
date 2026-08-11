"""Helpers for custom event-signup questions.

Answers live on ``EventSignup.custom_answers`` as a JSON dict keyed by
``str(question.id)``. Answer shapes by type:

- ``text``    -> ``str`` (capped at :data:`CUSTOM_ANSWER_TEXT_MAX`)
- ``single``  -> ``str`` (one of the question's options, else "")
- ``multi``   -> ``list[str]`` (subset of the question's options)
- ``boolean`` -> ``bool``

Design rules (see the design review): stored answers are treated as historical.
They are never re-validated against the current options, and out-of-range or
orphaned answers (from a deleted/edited question) degrade gracefully instead of
blocking a signup edit. On edit the answers dict is *merged*, never replaced, so
answers to questions not shown on the current form survive.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.events.models import SignupQuestion

if TYPE_CHECKING:
    from apps.events.models import Event, EventSignup

CUSTOM_ANSWER_TEXT_MAX = 2000
MAX_QUESTIONS_PER_EVENT = 30
MAX_OPTIONS_PER_QUESTION = 40


def field_name(question: SignupQuestion) -> str:
    """Return the POST field name for a question.

    Args:
        question: The signup question.

    Returns:
        The ``custom_q_<id>`` field name used in the signup form.

    """
    return f"custom_q_{question.pk}"


def active_questions(event: Event) -> list[SignupQuestion]:
    """Return the event's signup questions in display order.

    Args:
        event: The event.

    Returns:
        Ordered list of SignupQuestion.

    """
    return list(event.signup_questions.all())


def parse_custom_answers(
    event: Event,
    post,
    *,
    existing: dict | None = None,
) -> tuple[dict, str | None]:
    """Parse and validate custom-question answers from POST data.

    Only the event's currently-active questions are parsed; unknown ``custom_q_*``
    keys are ignored (defends against a question deleted between form render and
    submit). Required questions are enforced here, at submit time only.

    Args:
        event: The event whose questions to parse.
        post: The request POST QueryDict.
        existing: The signup's existing ``custom_answers`` to merge onto (edit
            flow). Answers for questions not shown on this form are preserved.

    Returns:
        Tuple ``(answers, error)``. ``answers`` maps ``str(question_id)`` to the
        parsed answer (merged onto ``existing``). ``error`` is a user-facing
        message on the first validation failure, else None (in which case
        ``answers`` should be ignored by the caller).

    """
    answers = dict(existing or {})
    prior = existing or {}
    for q in active_questions(event):
        key = field_name(q)
        qid = str(q.pk)
        present = False
        value: object = None
        if q.question_type == SignupQuestion.Type.TEXT:
            value = post.get(key, "").strip()[:CUSTOM_ANSWER_TEXT_MAX]
            if q.required and not value:
                return {}, f'Please answer "{q.label}".'
            present = bool(value)
        elif q.question_type == SignupQuestion.Type.BOOLEAN:
            value = post.get(key) is not None
            if q.required and not value:
                return {}, f'Please confirm "{q.label}".'
            present = value is True
        elif q.question_type == SignupQuestion.Type.SINGLE:
            # Grandfather the rider's previously-stored choice so an admin removing
            # an in-use option can't make an unrelated signup edit unsaveable.
            allowed = set(q.options)
            prior_val = prior.get(qid)
            if isinstance(prior_val, str) and prior_val:
                allowed.add(prior_val)
            value = post.get(key, "").strip()
            if value and value not in allowed:
                value = ""
            if q.required and not value:
                return {}, f'Please answer "{q.label}".'
            present = bool(value)
        elif q.question_type == SignupQuestion.Type.MULTI:
            allowed = set(q.options)
            prior_val = prior.get(qid)
            if isinstance(prior_val, list):
                allowed.update(v for v in prior_val if isinstance(v, str))
            chosen: list[str] = []
            for raw in post.getlist(key):
                v = raw.strip()
                if v and v in allowed and v not in chosen:
                    chosen.append(v)
            if q.required and not chosen:
                return {}, f'Please answer "{q.label}".'
            value = chosen
            present = bool(chosen)
        # Store only real answers so ``custom_answers`` keys mean "answered"
        # (keeps SignupQuestion.has_answers honest); clear the key otherwise.
        if present:
            answers[qid] = value
        else:
            answers.pop(qid, None)
    return answers, None


def resolve_signup_answers(signup: EventSignup, questions: list[SignupQuestion]) -> list[dict]:
    """Resolve a signup's stored answers into displayable rows for the given questions.

    Iterates the current questions (so a deleted question's orphaned answer is
    simply not shown). Never raises on a mismatched stored shape.

    Args:
        signup: The EventSignup holding ``custom_answers``.
        questions: The event's active questions (ordered).

    Returns:
        List of ``{"question", "label", "display", "answered"}`` dicts.

    """
    stored = signup.custom_answers or {}
    rows: list[dict] = []
    for q in questions:
        raw = stored.get(str(q.pk))
        if q.question_type == SignupQuestion.Type.BOOLEAN:
            if raw is True:
                display, answered = "Yes", True
            elif raw is False:
                display, answered = "No", True
            else:
                display, answered = "", False
        elif q.question_type == SignupQuestion.Type.MULTI:
            vals = raw if isinstance(raw, list) else []
            display, answered = ", ".join(str(v) for v in vals), bool(vals)
        else:  # text, single
            display = raw if isinstance(raw, str) else ""
            answered = bool(display)
        rows.append({"question": q, "label": q.label, "display": display, "answered": answered})
    return rows


def build_question_fields(questions: list[SignupQuestion], answers: dict | None = None) -> list[dict]:
    """Build per-question render context for the signup form (new or edit).

    Args:
        questions: The event's active questions (ordered).
        answers: The rider's existing ``custom_answers`` for prefill (edit flow),
            or None for a blank new-signup form.

    Returns:
        List of dicts with the question plus prefilled values by type:
        ``{"q", "text_value", "single_value", "multi_values", "bool_checked"}``.

    """
    answers = answers or {}
    fields: list[dict] = []
    for q in questions:
        raw = answers.get(str(q.pk))
        # Include any stored value that is no longer an offered option so the
        # rider's prior answer still renders (and survives an unrelated edit).
        options = list(q.options)
        if q.question_type == SignupQuestion.Type.SINGLE and isinstance(raw, str) and raw and raw not in options:
            options.append(raw)
        elif q.question_type == SignupQuestion.Type.MULTI and isinstance(raw, list):
            options.extend(v for v in raw if isinstance(v, str) and v and v not in options)
        fields.append({
            "q": q,
            "options": options,
            "text_value": raw if isinstance(raw, str) else "",
            "single_value": raw if isinstance(raw, str) else "",
            "multi_values": raw if isinstance(raw, list) else [],
            "bool_checked": raw is True,
        })
    return fields
