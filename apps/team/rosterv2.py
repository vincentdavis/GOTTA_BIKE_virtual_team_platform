"""The card roster's index: one allow-listed card per rider, and whose account it belongs to.

**This module is the privacy boundary of /team/rosterv2/.** Everything the page can show has
to come through ``build_roster_index``, and the page holds ~2,000 riders, most of whom never
registered here and never agreed to anything. Two rules make that safe, and both are
structural rather than a matter of remembering:

* **The SELECT list is the allow-list.** ``CARD_COLUMNS`` is the only thing handed to
  ``.values()``, and ``weight_kg`` and ``height_cm`` are not in it, so they never cross the
  database boundary. ``birth_year`` and ``email`` are not named anywhere in this file. Adding
  one is a visible diff to a constant whose docstring says why it may not happen.
* **The card is a frozen dataclass with a pinned field set.** No ``__dict__``, so nothing can
  attach a value later, and a test compares the fields to a literal tuple -- so a new field
  costs a deliberate edit to a test rather than arriving unnoticed.

``payload`` is the exception that proves the rule: it is selected, because the only source of
W/kg is inside it, and it is the one thing here that really does carry weight and height. It
is popped off the row and dropped before anything else happens, and no card holds a reference
to it.

The zwid is carried as ``_zwid`` with ``repr=False``. Both halves are needed. Django refuses
template variables beginning with an underscore, so ``{{ card._zwid }}`` is a compile-time
error -- but that guards attribute lookup by NAME only, and ``{{ cards }}`` renders each
element's repr. Measured: a plain frozen dataclass renders ``[Card(_zwid=8675309, ...)]``
from a template even with ``__str__`` defined, because the list's repr does not use it.
``repr=False`` is what actually closes it; ``__str__`` on every class in the chain is what
keeps the single-object case readable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import logfire
from constance import config
from django.db.models import Max

from apps.accounts.models import GuildMember, User
from apps.rider_data.models import RiderProfile
from apps.rider_data.services import zwids_to_refresh

if TYPE_CHECKING:
    from datetime import datetime

# Columns read from the cache. THIS TUPLE IS THE ALLOW-LIST -- see the module docstring.
# Deliberately absent, and not to be added without deciding the privacy question again:
# weight_kg, height_cm (the rider's body), last_requested_at and sources (cache mechanics),
# has_account and zwift_user_id (zauth service-wide state, which this page must not read),
# and ftp -- zftp is the value zauth resolves from three sources, while ftp is sparse.
CARD_COLUMNS = (
    "zwid",
    "name",
    "gender",
    "country",
    "age",
    "category_open",
    "category_women",
    "category_racing",
    "velo",
    "zwift_racing_score",
    "zp_skill",
    "compound_score",
    "phenotype_value",
    "zftp",
    "club_name",
    "last_race_at",
    "payload",
)

# The account side. No email, no birth_year, no first_name/last_name: nothing here needs a
# rider's real name yet, and the search box that will want one is a later step that has to
# widen this list on purpose. discord_id IS here and IS published -- a Discord avatar URL
# contains the snowflake, so choosing to show avatars is choosing to publish it.
ACCOUNT_COLUMNS = (
    "id",
    "zwid",
    "zwid_verified",
    "zwid_verification_method",
    "is_race_ready",
    "is_extra_verified",
    "discord_username",
    "discord_id",
    "discord_avatar",
)

GUILD_COLUMNS = ("user_id", "joined_at", "nickname", "display_name", "username")

# An ALLOW-list, so a bracket zauth invents later is hidden until somebody decides to show it.
# A deny-list fails the other way. The real vocabulary upstream is
# Snr / Jnr / U23 / Vet / Mas / 50+ / 60+ / 70+ / "-" / "", so what this hides is "-" and "".
#
# Jnr is SHOWN because Vincent said so ("Don't hide Jnr", 2026-09-11), overruling the plan,
# which had proposed hiding it so a minor is not marked out to the team. Recorded because the
# question is worth re-asking if the roster ever leaves team_member.
AGE_BRACKETS_SHOWN = frozenset({"Jnr", "U23", "Snr", "Vet", "Mas", "50+", "60+", "70+"})

# Duration keys on the zauth power curves, in SECONDS.
_20_MINUTES = "1200"
_1_MINUTE = "60"


@dataclass(frozen=True, slots=True)
class RiderCard:
    """What the page may know about one rider's racing.

    Every field here is renderable. The zwid is not: it is the join key and, later, an exact
    search term, and it is the one value on this object that must never reach the browser.

    Attributes:
        _zwid: Zwift id. ``repr=False`` plus the leading underscore keep it out of templates.
        name: The rider's Zwift name, or "Unknown rider" -- never their zwid as a name.
        gender: "male", "female" or "" -- three states. Blank is never rounded up to male.
        country: The raw upstream flag string, which includes subdivisions like "gb-wls".
        age_bracket: A bracket label from ``AGE_BRACKETS_SHOWN``, or "" -- never a number.
        zftp: Watts. Shown beside W/kg, which is the pair Vincent chose over weight.
        wkg_20min: 20-minute watts per kilo, as reported. NOT ftp divided by weight.
        wkg_1min: One-minute watts per kilo, as reported.
        last_race_at: Derived upstream from race results; stale for riders zauth has not
            re-fetched, which is why the 30-day counts come from ZwiftPower in a later step.

    """

    _zwid: int = field(repr=False)
    name: str = "Unknown rider"
    gender: str = ""
    country: str = ""
    age_bracket: str = ""
    category_open: str = ""
    category_women: str = ""
    category_racing: str = ""
    phenotype: str = ""
    velo: float | None = None
    velo_max90: float | None = None
    zwift_racing_score: float | None = None
    zp_skill: float | None = None
    compound_score: float | None = None
    zftp: float | None = None
    wkg_20min: float | None = None
    wkg_1min: float | None = None
    distance_km: float | None = None
    climbed_m: float | None = None
    club_name: str = ""
    last_race_at: datetime | None = None

    def __str__(self) -> str:
        """Return the rider's name, so rendering a card never falls back to its repr.

        Returns:
            The rider's name.

        """
        return self.name


@dataclass(frozen=True, slots=True)
class AccountFacts:
    """What a rider's account adds to their card, once it has been shown to be theirs.

    Only ever built for an account whose Zwift id verification is accepted -- see
    ``build_roster_index``. Read-only: nothing here recomputes race-ready status, because
    ``refresh_race_ready`` saves the row, and a page render must not write.

    Attributes:
        user_id: For linking to the rider's profile, and for joining later per-rider counts.
        discord_name: Server nickname, else display name, else username.
        avatar_url: Built from the Discord snowflake and avatar hash; "" when either is blank.
        member_since: When their CURRENT Discord membership began, or None.

    """

    user_id: int
    discord_name: str = ""
    avatar_url: str = ""
    is_race_ready: bool = False
    is_extra_verified: bool = False
    member_since: datetime | None = None

    def __str__(self) -> str:
        """Return the Discord name, so rendering the object never falls back to its repr.

        Returns:
            The rider's Discord name.

        """
        return self.discord_name


@dataclass(frozen=True, slots=True)
class RosterRow:
    """One card, with its account half when the rider has proved the zwid is theirs."""

    card: RiderCard
    account: AccountFacts | None = None

    def __str__(self) -> str:
        """Return the rider's name, never the pair's repr.

        Returns:
            The rider's name.

        """
        return self.card.name


@dataclass(frozen=True, slots=True)
class RosterIndex:
    """The whole roster, plus the few counts the page states about itself.

    Attributes:
        rows: One per rider, ordered by folded name with nameless riders last.
        synced_at: When rider stats last landed, for the header's freshness line.
        joined_count: How many cards carry an account half.
        contested_count: Zwids claimed by more than one verified account. Those cards get no
            account half at all, so the number is worth surfacing rather than swallowing.

    """

    rows: tuple[RosterRow, ...] = ()
    synced_at: datetime | None = None
    joined_count: int = 0
    contested_count: int = 0

    def __str__(self) -> str:
        """Return a count, never the repr of every row.

        Returns:
            A short description of the roster's size.

        """
        return f"{len(self.rows)} riders"

    @property
    def rider_count(self) -> int:
        """How many riders are on the roster.

        Returns:
            The number of cards.

        """
        return len(self.rows)


def _number(value: object) -> float | None:
    """Coerce a payload number, treating anything else as absent.

    Args:
        value: The raw value.

    Returns:
        A float, or None.

    """
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _block(payload: dict, *path: str) -> dict:
    """Walk into a payload by key, tolerating missing or wrongly-typed levels.

    Whole blocks arrive as null routinely -- a rider with no ZwiftRacing row has no power
    curve and no peak ratings -- so absence is the normal case, not an error.

    Args:
        payload: The ProfileFull document.
        *path: Successive dict keys.

    Returns:
        The nested dict, or an empty one.

    """
    node: object = payload
    for key in path:
        if not isinstance(node, dict):
            return {}
        node = node.get(key)
    return node if isinstance(node, dict) else {}


def _wkg(payload: dict, seconds: str) -> float | None:
    """Read one point off the watts-per-kilo curve.

    Reads ``curve_wkg`` and never ``curve_w``, which upstream builds from the same row over
    the same durations: dividing one by the other at the same duration returns the rider's
    stored weight exactly, so the two curves must never both reach a card.

    Rounded to one decimal, which is how it is shown -- a second decimal narrows the weight
    band recoverable from zFTP divided by W/kg for no gain to a reader.

    Args:
        payload: The ProfileFull document.
        seconds: The duration key, as a string of seconds.

    Returns:
        Watts per kilo, or None.

    """
    value = _number(_block(payload, "power", "curve_wkg").get(seconds))
    return round(value, 1) if value is not None else None


def _card(row: dict, payload: dict) -> RiderCard:
    """Build one card from an allow-listed row and its (discarded) payload.

    Args:
        row: A ``.values(*CARD_COLUMNS)`` row, with ``payload`` already removed.
        payload: The ProfileFull document, read here and not retained.

    Returns:
        The rider's card.

    """
    totals = _block(payload, "totals")
    metres = _number(totals.get("distance_km"))  # MISNAMED upstream: the value is metres.

    return RiderCard(
        _zwid=row["zwid"],
        # Never f"Rider {zwid}", the v1 fallback, which prints the zwid as a name.
        name=row["name"] or "Unknown rider",
        gender=row["gender"] or "",
        country=row["country"] or "",
        age_bracket=row["age"] if row["age"] in AGE_BRACKETS_SHOWN else "",
        category_open=row["category_open"] or "",
        category_women=row["category_women"] or "",
        category_racing=row["category_racing"] or "",
        phenotype=row["phenotype_value"] or "",
        velo=row["velo"],
        velo_max90=_number(_block(payload, "ratings").get("rating_max90")),
        zwift_racing_score=row["zwift_racing_score"],
        zp_skill=row["zp_skill"],
        compound_score=row["compound_score"],
        zftp=row["zftp"],
        wkg_20min=_wkg(payload, _20_MINUTES),
        wkg_1min=_wkg(payload, _1_MINUTE),
        distance_km=metres / 1000 if metres is not None else None,
        climbed_m=_number(totals.get("climbed_m")),
        club_name=row["club_name"] or "",
        last_race_at=row["last_race_at"],
    )


def _verified_claimants(*, zauth_required: bool) -> dict[int, list[dict]]:
    """Group accounts by the zwid they claim, keeping only accepted verifications.

    Filtering before grouping is the load-bearing order. Group first and an UNVERIFIED
    account claiming a teammate's zwid would make that zwid look contested and cost the
    teammate their account half -- one rider able to blank another's card by typing a number.

    The gate is the policy in ``apps.team.services.verification_accepted``, inlined only so
    the Constance read happens once instead of once per rider. Keep the two in step. Reading
    the raw ``zwid_verified`` column instead would look identical today, because
    ``ZAUTH_VERIFICATION_REQUIRED`` defaults off, and would silently ignore the cutover.

    Args:
        zauth_required: The hoisted ``ZAUTH_VERIFICATION_REQUIRED`` value.

    Returns:
        Accounts keyed by claimed zwid; a list, because the claim is not unique.

    """
    # zwid=0 is this app's "no zwid" sentinel, so zwid__isnull=False alone would collapse
    # every member who has not set one into a single identity.
    accounts = User.objects.filter(is_active=True, zwid__isnull=False, zwid__gt=0).values(*ACCOUNT_COLUMNS)

    claimants: dict[int, list[dict]] = {}
    for account in accounts:
        if not account["zwid_verified"]:
            continue
        if zauth_required and account["zwid_verification_method"] != "zauth":
            continue
        claimants.setdefault(account["zwid"], []).append(account)
    return claimants


def _guild_rows() -> dict[int, dict]:
    """Read current Discord memberships, keyed by user id.

    Open memberships only, so a returning member's card dates their current stint rather than
    a tenure they no longer have.

    Returns:
        One row per linked user with an open membership.

    """
    rows = GuildMember.objects.filter(user__isnull=False, date_left__isnull=True).values(*GUILD_COLUMNS)
    return {row["user_id"]: row for row in rows}


def _account_facts(account: dict, guild: dict | None) -> AccountFacts:
    """Assemble the account half of a card.

    Args:
        account: An ``ACCOUNT_COLUMNS`` row whose verification has already been accepted.
        guild: That user's open ``GuildMember`` row, if they have one.

    Returns:
        The account facts.

    """
    guild = guild or {}
    avatar = ""
    if account["discord_id"] and account["discord_avatar"]:
        avatar = f"https://cdn.discordapp.com/avatars/{account['discord_id']}/{account['discord_avatar']}.png"

    # Discord's own names, in the order a teammate would recognise. User.discord_nickname is
    # deliberately not consulted: it holds Discord's global_name and goes stale until the
    # rider next signs in.
    name = guild.get("nickname") or guild.get("display_name") or guild.get("username") or account["discord_username"]

    return AccountFacts(
        user_id=account["id"],
        discord_name=name or "",
        avatar_url=avatar,
        is_race_ready=bool(account["is_race_ready"]),
        is_extra_verified=bool(account["is_extra_verified"]),
        member_since=guild.get("joined_at"),
    )


def build_roster_index() -> RosterIndex:
    """Build the whole roster: who is on it, what may be shown, and whose account is whose.

    **Who is on it** is ``zwids_to_refresh()`` -- the same union that decides whose profile we
    keep current: members here, the ZwiftPower team page, and the ZwiftRacing club. Reusing it
    means the roster cannot disagree with the cache about who races for this team. The cache
    itself is NOT that set: ``purge_rider_profiles`` is deliberately unscheduled, so rows for
    riders who left stay forever, and a roster built from "every row we hold" would list them
    for good. A rider in the set with no cached row yet simply has no card.

    **Whose account is whose** is the verification gate, and it fails closed three ways: an
    unverified claim joins nothing, a zwid claimed by two verified accounts joins neither, and
    a rider with no account keeps their racing card without one. An unverified zwid is a number
    somebody typed -- attaching an account to it would put one rider's results under another
    rider's name and face.

    Note the gate answers "has this account been verified", which is weaker than "does this
    account own this zwid": ``manual_zwift_verify`` rewrites ``User.zwid`` without clearing
    ``zwid_verified``. Fixing that is out of this module's hands; the duplicate rule caps the
    damage at losing an account half rather than taking one over.

    Costs 8 queries, flat in the number of riders: three for the union, one Constance read for
    the cutover policy, accounts, guild memberships, the cache, and the freshness stamp.

    Returns:
        The roster, ordered by folded name with nameless riders last.

    """
    roster_zwids = zwids_to_refresh()
    zauth_required = config.ZAUTH_VERIFICATION_REQUIRED
    claimants = _verified_claimants(zauth_required=zauth_required)
    guild_rows = _guild_rows()

    rows: list[RosterRow] = []
    joined = 0
    contested = 0

    # order_by() clears Meta.ordering, which would sort in SQL on a name this then re-sorts
    # in Python -- and upstream does not strip whitespace, so SQL puts " Ada" first.
    cached = RiderProfile.objects.filter(zwid__in=roster_zwids).order_by().values(*CARD_COLUMNS)
    for row in cached.iterator(chunk_size=500):
        payload = row.pop("payload") or {}
        card = _card(row, payload)

        claims = claimants.get(card._zwid, ())
        account = None
        if len(claims) == 1:
            account = _account_facts(claims[0], guild_rows.get(claims[0]["id"]))
            joined += 1
        elif len(claims) > 1:
            contested += 1
            logfire.error(
                "Roster zwid claimed by more than one verified account",
                zwid=card._zwid,  # ids only, per the logging rule
                user_ids=[claim["id"] for claim in claims],
            )
        rows.append(RosterRow(card=card, account=account))

    # Folded, so case and the whitespace upstream does not strip cannot reorder the page;
    # the zwid tiebreak keeps two riders of the same name in the same order on every request,
    # which is what stops paging showing one of them twice. Which values sort LAST is a
    # question for the sort controls, not for the index.
    rows.sort(key=lambda row: (row.card.name.casefold(), row.card._zwid))

    logfire.info(
        "Built roster index",
        riders=len(rows),
        joined=joined,
        contested=contested,
        requested=len(roster_zwids),
    )
    return RosterIndex(
        rows=tuple(rows),
        synced_at=RiderProfile.objects.aggregate(synced_at=Max("fetched_at"))["synced_at"],
        joined_count=joined,
        contested_count=contested,
    )
