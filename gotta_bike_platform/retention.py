"""Declared data-retention policy, attached to the models it governs.

Most tables in this project have never had a retention decision made about them, which in
practice means "keep forever" -- not as a choice, but as the default nobody set. This module
exists so that stops being invisible: a model either declares what happens to its rows over
time, or it appears in the unclassified list in ``test_retention_policy.py``. There is no
third state, and the list may only shrink.

The declaration lives on the model rather than in a document because a document drifts. This
repository has the evidence: ``TODO.md`` still carries a heading reading "no test coverage
exists" above a suite of well over a thousand tests. A policy that disagrees with the code is
worse than no policy, because it is a claim about people's data that cannot be honoured.

Usage::

    class ZwiftRoute(models.Model):
        retention = RetentionPolicy.keep("Reference data, no personal content.")

The human-readable page is meant to be generated from these declarations, so it cannot
disagree with them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class RetentionPolicy:
    """What happens to a model's rows as they age.

    Built through the classmethods rather than directly, so that each kind carries the fields
    it actually needs and no others.

    Attributes:
        kind: One of the ``KIND_*`` constants.
        reason: Why this is the right answer for this model. Required for every kind,
            including ``keep`` -- "we never got round to it" and "keeping this is correct"
            look identical in a table, and only the reason distinguishes them.
        anchor: Field the age is measured from. ``None`` for ``keep`` and ``cascade``.
        setting: Constance setting holding the window, by convention ``*_DAYS``.
        task: Name of the registered task that enforces this, in
            ``gotta_bike_platform.task_registry``.

    """

    KIND_KEEP: ClassVar[str] = "keep"
    KIND_CASCADE: ClassVar[str] = "cascade"
    KIND_STRIP: ClassVar[str] = "strip"
    KIND_DELETE: ClassVar[str] = "delete"

    kind: str
    reason: str
    anchor: str | None = None
    setting: str | None = None
    task: str | None = None

    @classmethod
    def keep(cls, reason: str) -> RetentionPolicy:
        """Rows are kept indefinitely, deliberately.

        For reference data, configuration, and records whose whole purpose is to persist.
        Ageing these out would be destructive rather than protective: a route row is an FK
        target for saved plans, a CMS page that vanished is a bug, not a privacy win.

        Args:
            reason: Why keeping is correct here -- specifically, why there is nothing
                personal to age out, or what would break if rows were removed.

        Returns:
            The policy.

        """
        return cls(kind=cls.KIND_KEEP, reason=reason)

    @classmethod
    def cascade(cls, reason: str) -> RetentionPolicy:
        """Rows die with the account they belong to, and need no clock of their own.

        Args:
            reason: How the rows are reached -- normally the CASCADE path to ``User``.

        Returns:
            The policy.

        """
        return cls(kind=cls.KIND_CASCADE, reason=reason)

    @classmethod
    def strip(cls, reason: str, *, anchor: str, setting: str, task: str | None = None) -> RetentionPolicy:
        """Clear the sensitive payload after a window, keeping the row itself.

        The house pattern, and usually the right one where a row has value beyond the personal
        data on it -- roster history, an audit trail, a dashboard's underlying counts.

        Args:
            reason: What is cleared, what is kept, and why the row is worth keeping.
            anchor: Field the window is measured from.
            setting: Constance setting holding the window in days.
            task: Registered task that enforces it, once one exists.

        Returns:
            The policy.

        """
        return cls(kind=cls.KIND_STRIP, reason=reason, anchor=anchor, setting=setting, task=task)

    @classmethod
    def delete(cls, reason: str, *, anchor: str, setting: str, task: str | None = None) -> RetentionPolicy:
        """Rows are removed outright after a window.

        Args:
            reason: Why deletion rather than stripping -- normally that the identifier is the
                row, so there is nothing left worth keeping once it goes.
            anchor: Field the window is measured from.
            setting: Constance setting holding the window in days.
            task: Registered task that enforces it, once one exists.

        Returns:
            The policy.

        """
        return cls(kind=cls.KIND_DELETE, reason=reason, anchor=anchor, setting=setting, task=task)


def policy_for(model: type) -> RetentionPolicy | None:
    """Return the declared policy for ``model``, if it has one.

    django-simple-history generates ``Historical*`` models that no one hand-writes, so they
    cannot carry their own declaration -- and they are not a footnote here: the two shadow
    tables in this project hold roughly thirty times the rows of the live tables they track,
    all of it third-party biometric data. Their policy is their subject's, resolved here, so
    classifying a tracked model classifies its history along with it.

    Note that simple_history keeps history rows when the parent row is deleted, by design. Any
    sweep acting on a tracked model has to remove the shadow rows itself.

    Args:
        model: The model class.

    Returns:
        The policy, or None if the model has not been classified.

    """
    subject = getattr(model, "instance_type", None)
    if subject is not None:
        return getattr(subject, "retention", None)
    return getattr(model, "retention", None)
