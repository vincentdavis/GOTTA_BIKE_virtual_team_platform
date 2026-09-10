"""Team kit status: labels, choices and the read/write helpers around ``User.team_kit``.

``User.team_kit`` is a JSON object ``{kit_slug: status}``. A kit with no entry is simply
``unknown`` -- so nothing needs backfilling when a kit is added, and a rider who has never
touched it reads as "What's a kit".

The same stored status is worded differently depending on who is looking. Riders pick from
three; the team can set all five, including the two that only make sense from the team's
side of a Zwift order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.team.models import KitStatus, TeamKit

if TYPE_CHECKING:
    from apps.accounts.models import User

# What a rider sees for their own status. The Zwift-side states have no rider wording of
# their own; the team's label ("Submitted to Zwift") is already the right thing to tell them.
RIDER_LABELS: dict[str, str] = {
    KitStatus.UNKNOWN: "What's a kit",
    KitStatus.NEED: "I need the kit",
    KitStatus.HAVE: "I have the kit",
}

# The three a rider may choose. Submitted and Completed describe a Zwift order the rider
# cannot see into, so they are set by the team (and, later, by the planned automations).
RIDER_CHOICES: tuple[str, ...] = (KitStatus.UNKNOWN, KitStatus.NEED, KitStatus.HAVE)

DEFAULT_STATUS: str = KitStatus.UNKNOWN

# Badge colour per status on the profile. Solid badges only: they use DaisyUI's paired
# *-content foreground, which stays readable in both themes where a raw colour-as-text does
# not (see the contrast work on the captain banner).
BADGE_CLASSES: dict[str, str] = {
    KitStatus.UNKNOWN: "badge-ghost",
    KitStatus.NEED: "badge-warning",
    KitStatus.SUBMITTED: "badge-info",
    KitStatus.COMPLETED: "badge-info",
    KitStatus.HAVE: "badge-success",
}


def rider_label(status: str) -> str:
    """Word a status the way the rider should read it.

    Args:
        status: A stored ``KitStatus`` value.

    Returns:
        The rider-facing label, falling back to the team's label for team-only states.

    """
    return RIDER_LABELS.get(status) or KitStatus(status).label


def status_for(user: User, kit: TeamKit) -> str:
    """Return a rider's stored status for one kit.

    Args:
        user: The rider.
        kit: The kit.

    Returns:
        The stored status, or the default when the rider has no entry for it. An entry
        holding a value that is not a valid status is treated as the default rather than
        raising, since the field is JSON and could be edited by hand.

    """
    value = (user.team_kit or {}).get(kit.slug)
    return value if value in KitStatus.values else DEFAULT_STATUS


def active_kits() -> list[TeamKit]:
    """Return the kits riders are currently shown and asked about.

    Returns:
        Active kits in display order.

    """
    return list(TeamKit.objects.filter(active=True))


def kit_rows(user: User, kits: list[TeamKit] | None = None) -> list[dict]:
    """Build the display rows for a rider's kits, for the profile.

    Args:
        user: The rider.
        kits: Kits to show, defaulting to the active ones. Passed in so a caller rendering
            several riders need not re-query.

    Returns:
        One dict per kit with ``kit``, ``status``, ``label`` and ``badge``.

    """
    rows = []
    for kit in active_kits() if kits is None else kits:
        status = status_for(user, kit)
        rows.append({
            "kit": kit,
            "status": status,
            "label": rider_label(status),
            "badge": BADGE_CLASSES.get(status, "badge-ghost"),
        })
    return rows


def field_name(kit: TeamKit) -> str:
    """Name of the form field that edits one kit's status.

    Args:
        kit: The kit.

    Returns:
        A field name that cannot collide with a User model field.

    """
    return f"team_kit__{kit.slug}"


def build_kit_fields(user: User, *, for_team: bool, kits: list[TeamKit] | None = None) -> dict:
    """Build one form field per kit, pre-set to the rider's current status.

    Shared by the rider's profile form and the user admin, which differ only in what they may
    choose: a rider gets three statuses, the team gets all five.

    A rider whose kit is at a team-set status ("Submitted to Zwift") also gets that status as
    an option, clearly marked. Without it, the select could not represent the current value,
    so the rider saving an unrelated change -- their timezone, say -- would silently move the
    kit back to one of their three. This is the same grandfathering the signup questions use
    for an option removed after a rider picked it.

    Args:
        user: The rider whose statuses seed the fields.
        for_team: True for the admin's full set, False for the rider's three.
        kits: Kits to build for, defaulting to the active ones.

    Returns:
        ``{field_name: ChoiceField}``, in display order.

    """
    from django import forms

    fields = {}
    for kit in active_kits() if kits is None else kits:
        current = status_for(user, kit)
        if for_team:
            choices = [(status.value, status.label) for status in KitStatus]
        else:
            choices = [(status, RIDER_LABELS[status]) for status in RIDER_CHOICES]
            if current not in RIDER_CHOICES:
                choices.insert(0, (current, f"{KitStatus(current).label} (set by the team)"))
        fields[field_name(kit)] = forms.ChoiceField(
            label=kit.name,
            choices=choices,
            initial=current,
            required=False,
            help_text=kit.description,
        )
    return fields


def apply_kit_fields(user: User, data, cleaned_data: dict, kits: list[TeamKit] | None = None) -> bool:
    """Merge submitted kit statuses into ``user.team_kit`` without saving.

    Only kits whose field was actually SUBMITTED are touched. Two different pages post to the
    profile form, and "absent from the POST" has to mean "leave it alone" -- never "reset it"
    -- or any page that does not render the kit fields would wipe them on save. Entries for
    kits that are inactive or no longer offered are likewise kept rather than dropped.

    Args:
        user: The rider to update in place.
        data: The raw submitted data (``form.data``), used to tell "not sent" from "sent".
        cleaned_data: The validated form data.
        kits: Kits the form rendered, defaulting to the active ones.

    Returns:
        True if any status changed.

    """
    team_kit = dict(user.team_kit or {})
    changed = False
    for kit in active_kits() if kits is None else kits:
        name = field_name(kit)
        if name not in data:
            continue
        value = cleaned_data.get(name)
        if value not in KitStatus.values:
            continue
        if team_kit.get(kit.slug) != value:
            team_kit[kit.slug] = value
            changed = True
    if changed:
        user.team_kit = team_kit
    return changed
