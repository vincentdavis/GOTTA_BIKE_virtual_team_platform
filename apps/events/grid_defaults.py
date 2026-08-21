"""Event-level defaults and enforcement for the availability builder's toggles.

An event may set a default for each toggle and, separately, enforce it. The two are
independent: a default seeds a new grid and can be changed; an enforced default locks
the control, in both directions, so an event can require a setting be *off* as well
as on.

Both the builder and the save go through :func:`resolve`, because the builder posts
JSON -- a disabled checkbox is a client-side courtesy, not a constraint, and anything
enforced has to be re-applied server-side or it is decoration.

Deliberately not retroactive. Changing an event default seeds new grids and takes
effect when an existing grid is next saved; it does not rewrite grids captains have
already published behind their backs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.events.models import Event

# The AvailabilityGrid boolean fields an event can govern.
SETTINGS = (
    "max_races_question",
    "rest_days_question",
    "hide_empty_days",
    "single_slot",
    "expanded_features",
)


def default_for(event: Event, setting: str) -> bool | None:
    """Return the event's default for one setting.

    Args:
        event: The parent event.
        setting: One of :data:`SETTINGS`.

    Returns:
        True/False when the event sets a default, or None when it does not.

    """
    return getattr(event, f"grid_default_{setting}", None)


def is_enforced(event: Event, setting: str) -> bool:
    """Report whether the event locks a setting to its default.

    An enforce flag with no default set is meaningless and treated as not enforced --
    there is nothing to lock the control to.

    Args:
        event: The parent event.
        setting: One of :data:`SETTINGS`.

    Returns:
        True when the setting is locked.

    """
    return bool(getattr(event, f"grid_enforce_{setting}", False)) and default_for(event, setting) is not None


def resolve(event: Event, setting: str, submitted: bool) -> bool:
    """Decide a setting's saved value, applying enforcement.

    Args:
        event: The parent event.
        setting: One of :data:`SETTINGS`.
        submitted: What the builder sent.

    Returns:
        The value to store.

    """
    if is_enforced(event, setting):
        return bool(default_for(event, setting))
    return bool(submitted)


def initial_values(event: Event) -> dict[str, bool | None]:
    """Seed values for a *new* grid, as the builder should show them.

    Args:
        event: The parent event.

    Returns:
        ``{setting: True/False}`` for settings the event defaults, others omitted so
        the builder keeps its own starting value.

    """
    return {s: default_for(event, s) for s in SETTINGS if default_for(event, s) is not None}


def enforced_map(event: Event) -> dict[str, bool]:
    """Which settings are locked, for the builder to disable.

    Args:
        event: The parent event.

    Returns:
        ``{setting: True}`` for each enforced setting.

    """
    return {s: True for s in SETTINGS if is_enforced(event, s)}
