"""Facet definitions for filtering event signups by their custom-question answers.

The signups table filters client-side, so this module's only job is to describe the
*buckets* a question offers and to hand the browser a compact, correctly-typed payload
of who answered what. Matching, counting and sorting happen in the page's JS over the
already-rendered rows — there is no pagination, so a client-side filter sees every row.

Two properties of the stored data drive the shape of everything here:

- **A blank answer stores no key at all** (see :mod:`apps.events.signup_questions`), so
  "no key" is the only representation of "did not answer". That is why every question
  gets an explicit *no answer* bucket rather than a blank option.
- **A boolean's ``False`` is never stored.** ``parse_custom_answers`` writes the key only
  when the value is ``True``, so an unchecked box is byte-identical to never answering.
  A boolean therefore offers **two** buckets, not three, and the negative one is labelled
  "Not confirmed (or no answer)" — a ``No`` bucket would read 0 on every event forever.

Stored answers are historical and never re-validated against the current options, so a
rider can hold a value an admin has since deleted. Those values are surfaced as extra
"(removed)" buckets instead of silently becoming unfilterable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.events.models import SignupQuestion

if TYPE_CHECKING:
    from apps.events.models import EventSignup

#: Querystring/DOM value marking the "did not answer" bucket.
NONE_VALUE = "__none__"
#: Querystring/DOM value for "answered anything" (boolean and text questions).
ANSWERED_VALUE = "__answered__"
#: Above this many questions the facet panel renders collapsed, so it can't bury the table.
PANEL_OPEN_MAX_QUESTIONS = 6


def _stored(signup: EventSignup, question: SignupQuestion) -> object:
    """Return a signup's raw stored answer for one question.

    Args:
        signup: The signup holding ``custom_answers``.
        question: The question to look up.

    Returns:
        The raw stored value, or None when the key is absent.

    """
    return (signup.custom_answers or {}).get(str(question.pk))


def normalized_answer(signup: EventSignup, question: SignupQuestion) -> object | None:
    """Normalize one stored answer into the value the client filters on.

    Text and boolean questions collapse to ``True`` — the page filters them only by
    answered/not, and the readable text is already in the table cell, so sending it
    again would inflate every row for nothing.

    Args:
        signup: The signup holding ``custom_answers``.
        question: The question to normalize.

    Returns:
        ``True`` for an answered boolean/text question, the option string for single,
        a list of option strings for multi, or None when unanswered. None is the signal
        to omit the key entirely.

    """
    raw = _stored(signup, question)
    if question.question_type == SignupQuestion.Type.BOOLEAN:
        return True if raw is True else None
    if question.question_type == SignupQuestion.Type.MULTI:
        vals = [v for v in raw if isinstance(v, str) and v] if isinstance(raw, list) else []
        return vals or None
    if question.question_type == SignupQuestion.Type.SINGLE:
        return raw if isinstance(raw, str) and raw else None
    # text
    return True if isinstance(raw, str) and raw else None


def answers_payload(signups: list[EventSignup], questions: list[SignupQuestion]) -> dict[str, dict[str, object]]:
    """Build the ``{signup_pk: {question_pk: value}}`` map the page's JS filters on.

    Unanswered questions are omitted rather than stored as a falsy value, mirroring
    ``custom_answers`` itself so "missing key" stays the single meaning of "unanswered".

    Args:
        signups: The signups to include.
        questions: The event's active questions.

    Returns:
        Nested dict keyed by stringified primary keys, JSON-serializable as-is.

    """
    payload: dict[str, dict[str, object]] = {}
    for signup in signups:
        row: dict[str, object] = {}
        for q in questions:
            value = normalized_answer(signup, q)
            if value is not None:
                row[str(q.pk)] = value
        payload[str(signup.pk)] = row
    return payload


def _extra_values(question: SignupQuestion, signups: list[EventSignup]) -> list[str]:
    """Find stored choices for a question that are no longer among its options.

    Args:
        question: A single- or multi-choice question.
        signups: The signups to scan.

    Returns:
        Sorted list of stored values absent from ``question.options``.

    """
    known = set(question.options or [])
    found: set[str] = set()
    for signup in signups:
        value = normalized_answer(signup, question)
        if isinstance(value, str):
            found.add(value)
        elif isinstance(value, list):
            found.update(value)
    return sorted(found - known)


def build_facets(questions: list[SignupQuestion], signups: list[EventSignup]) -> list[dict]:
    """Describe each question as a list of filterable buckets.

    Args:
        questions: The event's active questions, in display order.
        signups: The signups being shown, used only to surface removed-option values.

    Returns:
        One dict per question: ``{"pk", "label", "type", "buckets"}``, where each bucket
        is ``{"value", "label", "retired"}``. The final bucket is always the no-answer one.

    """
    facets: list[dict] = []
    for q in questions:
        buckets: list[dict] = []
        if q.question_type == SignupQuestion.Type.BOOLEAN:
            buckets.append({"value": ANSWERED_VALUE, "label": "Confirmed", "retired": False})
            none_label = "Not confirmed (or no answer)"
        elif q.question_type == SignupQuestion.Type.TEXT:
            buckets.append({"value": ANSWERED_VALUE, "label": "Answered", "retired": False})
            none_label = "No answer"
        else:
            buckets.extend({"value": o, "label": o, "retired": False} for o in (q.options or []))
            buckets.extend(
                {"value": v, "label": f"{v} (removed)", "retired": True} for v in _extra_values(q, signups)
            )
            none_label = "No answer"
        buckets.append({"value": NONE_VALUE, "label": none_label, "retired": False})
        facets.append({
            "pk": q.pk,
            "label": q.label,
            "type": q.question_type,
            "buckets": buckets,
        })
    return facets


def panel_starts_open(questions: list[SignupQuestion]) -> bool:
    """Decide whether the facet panel renders expanded.

    An event may have up to 30 questions; expanding all of them would push the table
    off the screen, so the panel only starts open for a short list.

    Args:
        questions: The event's active questions.

    Returns:
        True when the panel should render expanded.

    """
    return 0 < len(questions) <= PANEL_OPEN_MAX_QUESTIONS
