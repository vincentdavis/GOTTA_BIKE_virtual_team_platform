"""Team kit status: labels, choices and the read/write helpers around ``User.team_kit``.

``User.team_kit`` is a JSON object ``{kit_slug: status}``. A kit with no entry is simply
``unknown`` -- so nothing needs backfilling when a kit is added, and a rider who has never
touched it reads as "What's a kit".

The same stored status is worded differently depending on who is looking. Riders pick from
three; the team can set all five, including the two that only make sense from the team's
side of a Zwift order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from django.db.models import Q

from apps.team.models import KitStatus, TeamKit

if TYPE_CHECKING:
    from collections.abc import Collection

    from apps.accounts.models import User

# Matches User.VerificationMethod.ZAUTH; a literal so this module need not import the User
# model at load time (apps.zwift.verification does the same).
ZAUTH = "zauth"

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


def can_manage_team_kit(user) -> bool:
    """Whether a user may use the team kit page and its actions.

    Superusers and app admins, as for all of /site/config/, plus membership admins -- getting
    kits to riders is membership work. Membership admins get THIS page only: the rest of
    /site/config/ holds the Discord bot token, API credentials and permission mappings, and
    stays app-admin-only. Kept in one place so the page, every action and the sidebar cannot
    come to disagree about who is allowed.

    Args:
        user: The requesting user.

    Returns:
        True if they may manage team kits.

    """
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(user.is_superuser or user.is_app_admin or user.is_membership_admin)


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


def current_kit() -> TeamKit | None:
    """Return the kit the team is currently getting everyone into.

    The single place future automations and exports should ask "which kit?", so a new
    season's kit is picked up by making it current rather than by editing code.

    Returns:
        The current kit, or None if none is set.

    """
    return TeamKit.objects.filter(is_current=True).first()


def team_members():
    """Everyone this page treats as a team member: anyone with a Discord login.

    The definition the team gave, and the one that makes a "hasn't answered" count honest.
    Discord OAuth is the only login for riders, so this excludes accounts that never signed
    in that way -- chiefly locally-created superusers -- while including everyone else.

    Returns:
        A queryset of users with a Discord id.

    """
    from apps.accounts.models import User

    return User.objects.exclude(discord_id="")


def zauth_verified_q() -> Q:
    """Select members whose Zwift account is verified through zauth (the official Zwift OAuth).

    What "verified" means on the team kit page -- its filter, its column and its export all
    use this rule. Two stored fields, both required:

    - ``zwid_verification_method == "zauth"``. Legacy (Sauce mod) and admin verifications are
      still accepted elsewhere, but they are what the zauth migration is replacing, and a kit
      goes to the Zwift account zauth vouches for.
    - ``zwid_verified``. The method alone is not enough: a rider removing their own
      verification (``unverify_zwift``) clears ``zwid_verified`` but leaves the method at
      "zauth". So ``User.is_zauth_verified``, which reads the method only, still says yes
      for them. This page does not.

    Never the live zauth connection, nor ``has_account``: the platform records the
    verification (``apps.zwift.verification`` keeps it in step with the service), and asking
    the service per row would also make the page depend on it being up.

    Returns:
        The filter.

    """
    return Q(zwid_verified=True, zwid_verification_method=ZAUTH)


def verified_through_zauth(member: User) -> bool:
    """Apply ``zauth_verified_q`` to a member already in memory.

    Args:
        member: The member, with ``zwid_verified`` and ``zwid_verification_method`` loaded.

    Returns:
        True if their Zwift account is verified through zauth.

    """
    return bool(member.zwid_verified and member.zwid_verification_method == ZAUTH)


def _join(parts: list[str], conjunction: str = "and") -> str:
    """Join phrases as a sentence would: "a", "a and b", "a, b and c".

    Args:
        parts: The phrases.
        conjunction: The word before the last one -- "and", or "or" for alternatives.

    Returns:
        The joined phrase.

    """
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} {conjunction} {parts[-1]}"


class MemberFilters(NamedTuple):
    """The team kit page's member-list filters. Each narrows the list; together they combine.

    The field names are ``kit_member_rows``'s keyword arguments, so the filters can be passed
    straight through with ``**filters._asdict()``.
    """

    verified_only: bool = False  # Zwift account verified through zauth -- zauth_verified_q
    race_verified_only: bool = False  # Race Verified -- the cached User.is_race_ready
    # Current-kit statuses to keep, in KitStatus order; empty means any. A member matches if
    # their status is any one of them.
    statuses: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        """Whether any filter is on.

        Returns:
            True if the list is narrowed at all.

        """
        return self.verified_only or self.race_verified_only or bool(self.statuses)

    def query(self) -> list[tuple[str, str]]:
        """Write the filters back as query parameters, for the export link.

        Built from the parsed filters rather than passing the request's query string through,
        so the export carries exactly the filters the page applied.

        Returns:
            ``(name, value)`` pairs -- a list, since ``status`` repeats once per status.

        """
        pairs = []
        if self.verified_only:
            pairs.append(("verified", "1"))
        if self.race_verified_only:
            pairs.append(("race_verified", "1"))
        pairs.extend(("status", status) for status in self.statuses)
        return pairs

    def describe(self, kit_name: str) -> str:
        """Say what a filtered list is showing, for "Showing 3 of 40 members -- ...".

        Args:
            kit_name: The current kit's name, for the status filter.

        Returns:
            A phrase such as "verified with zauth and race verified, whose 2026 Race Kit status
            is Need kit or Submitted to Zwift" -- statuses in the team's own labels, the ones
            the list's status column shows.

        """
        qualities = []
        if self.verified_only:
            qualities.append("verified with zauth")
        if self.race_verified_only:
            qualities.append("race verified")
        phrase = _join(qualities) if qualities else ""
        if self.statuses:
            labels = _join([KitStatus(status).label for status in self.statuses], "or")
            clause = f"whose {kit_name} status is {labels}"
            phrase = f"{phrase}, {clause}" if phrase else clause
        return phrase


def member_filters(params, current: TeamKit | None) -> MemberFilters:
    """Read the member-list filters from a query string.

    Shared by the page and its CSV export, so "Export CSV" always downloads the list on screen.

    Args:
        params: The request's GET parameters.
        current: The current kit, or None.

    Returns:
        The filters. Statuses are those of the current kit, so without one they are ignored --
        the page disables them -- rather than emptying the list for no visible reason. A value
        that is not a status is ignored too. ``?need=1``, this filter's only option before it
        took several, still means "Need kit", so old links and bookmarks keep working.

    """
    requested = set(params.getlist("status"))
    if params.get("need") == "1":
        requested.add(KitStatus.NEED)
    return MemberFilters(
        verified_only=params.get("verified") == "1",
        race_verified_only=params.get("race_verified") == "1",
        statuses=tuple(s for s in KitStatus.values if s in requested) if current is not None else (),
    )


def kit_status_counts(kits: list[TeamKit]) -> dict[str, dict[str, int]]:
    """Count team members at each status, per kit, in one query.

    Includes "unknown" -- members with no answer for that kit -- now that "team member" has a
    filterable definition (``team_members``). Before it did, that number had no honest
    denominator and was left out rather than shown wrong. Every count on the team kit page
    uses this same population, so the summary and the member list cannot disagree.

    Args:
        kits: The kits to count for.

    Returns:
        ``{kit_slug: {status: count}}`` for every KitStatus, including unknown.

    """
    counts = {kit.slug: dict.fromkeys(KitStatus.values, 0) for kit in kits}
    members = 0
    for team_kit in team_members().values_list("team_kit", flat=True):
        members += 1
        for slug, status in (team_kit or {}).items():
            # UNKNOWN is derived below; an entry explicitly set to it, or holding junk from a
            # hand edit, must not be counted as an answer.
            if slug in counts and status in KitStatus.values and status != KitStatus.UNKNOWN:
                counts[slug][status] += 1
    for by_status in counts.values():
        answered = sum(n for status, n in by_status.items() if status != KitStatus.UNKNOWN)
        by_status[KitStatus.UNKNOWN] = members - answered
    return counts


def _verification_method(member: User) -> dict[str, str]:
    """Say how a member's current Zwift verification was obtained, for the column and export.

    Only a current verification has a method worth showing. A method left behind after the
    verification was removed (see ``zauth_verified_q``) reads as not verified.

    Args:
        member: The member.

    Returns:
        ``verification_method`` -- "zauth", "legacy", "admin", "other" for a verification
        with no recorded method, or "" when not verified -- and ``verification_label``, the
        wording the page shows ("" when not verified).

    """
    if not member.zwid_verified:
        return {"verification_method": "", "verification_label": ""}
    method = member.zwid_verification_method or "other"
    label = member.get_zwid_verification_method_display() if member.zwid_verification_method else "Other"
    return {"verification_method": method, "verification_label": label}


def kit_member_rows(
    *,
    verified_only: bool = False,
    race_verified_only: bool = False,
    statuses: Collection[str] = (),
    kit: TeamKit | None = None,
) -> list[dict]:
    """Build the team member list for the team kit page.

    A fixed number of queries however many members there are: one for the members, one
    each for the ZwiftPower and ZwiftRacing names, and nothing per row -- each row's kit
    status is read from the already-loaded ``team_kit``.

    The Zwift name follows the team roster exactly (``UnifiedRider.display_name``): the
    ZwiftPower name, else the ZwiftRacing name. Using a different source here would have the
    two pages disagree about what a rider is called.

    Args:
        verified_only: Limit to members whose Zwift account is verified through zauth
            (``zauth_verified_q``) -- not by the legacy or admin methods.
        race_verified_only: Limit to members who are Race Verified, read from the cached
            ``User.is_race_ready`` -- what the roster's Race Verified filter, event
            eligibility and the Discord race-ready role all use, so the pages agree. Not
            recalculated here: that would be queries per rider, and the cache is kept current
            by ``refresh_race_ready`` and the scheduled sweep. (Display badges rank Extra
            Verified above it, so a rider who is Extra Verified but not race ready shows "EV"
            on their profile yet is left out here, as by the roster's filter.)
        statuses: Limit to members whose status for ``kit`` is any of these ``KitStatus``
            values; empty for any status. "unknown" includes members who never answered.
            Ignored when no kit is given, since there is no status to match.
        kit: The kit to report status for, normally the current one; None for no column.

    Returns:
        Rows sorted by Discord name, each with ``user``, ``discord_name``, ``zwift_name``,
        ``zwid``, ``zauth_verified``, ``verification_method`` (the stored method of a current
        verification: "zauth", "legacy", "admin", "other" when it has none, or "" when not
        verified), ``verification_label`` and -- when a kit is given -- ``status``, ``label``
        and ``badge``.

    """
    from apps.zwiftpower.models import ZPTeamRiders
    from apps.zwiftracing.models import ZRRider

    queryset = team_members()
    if verified_only:
        queryset = queryset.filter(zauth_verified_q())
    if race_verified_only:
        queryset = queryset.filter(is_race_ready=True)
    # zwid_verification_method is loaded with the rest: left deferred, every row would fetch it.
    members = list(
        queryset.only(
            "id",
            "discord_username",
            "discord_nickname",
            "zwid",
            "zwid_verified",
            "zwid_verification_method",
            "team_kit",
        )
    )

    zwids = {member.zwid for member in members if member.zwid}
    zp_names = dict(ZPTeamRiders.objects.filter(zwid__in=zwids).values_list("zwid", "name")) if zwids else {}
    zr_names = dict(ZRRider.objects.filter(zwid__in=zwids).values_list("zwid", "name")) if zwids else {}

    rows = []
    for member in members:
        row = {
            "user": member,
            "discord_name": member.discord_nickname or member.discord_username,
            "discord_username": member.discord_username,
            "zwift_name": zp_names.get(member.zwid) or zr_names.get(member.zwid) or "",
            "zwid": member.zwid,
            "zauth_verified": verified_through_zauth(member),
            **_verification_method(member),
        }
        if kit is not None:
            status = status_for(member, kit)
            row.update(status=status, label=KitStatus(status).label, badge=BADGE_CLASSES.get(status, "badge-ghost"))
        rows.append(row)
    # Filtered here, on the already-loaded rows, rather than with a JSON key lookup in the
    # query. A slug may contain "__", which Django would read as a lookup separator in
    # team_kit__<slug>; "unknown" has to match riders with no entry (or a junk one), which
    # status_for already folds in; and the rows are in memory anyway -- so this is safer,
    # simpler and free.
    if statuses and kit is not None:
        wanted = set(statuses)
        rows = [row for row in rows if row["status"] in wanted]
    rows.sort(key=lambda row: (row["discord_name"] or "").lower())
    return rows


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
            "is_current": kit.is_current,
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
