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

The zwid is renderable, and deliberately so: the roster this page replaces already prints
"ZWID: ..." for every rider in its tooltip, alongside ZwiftPower and ZwiftRacing links, and
this page's own search box takes one. It is Zwift's public id for a rider, not a secret we
hold. It was carried as ``_zwid`` while the card was being built, which is why the trick
below is worth keeping written down for the fields that still use it.

``_search`` on ``RosterRow`` is such a field, and it needs BOTH halves. Django refuses
template variables beginning with an underscore, so ``{{ row._search }}`` is a compile-time
error -- but that guards attribute lookup by NAME only, and ``{{ rows }}`` renders each
element's repr. Measured: a plain frozen dataclass renders ``[Card(zwid=8675309, ...)]``
from a template even with ``__str__`` defined, because the list's repr does not use it.
``repr=False`` is what actually closes it; ``__str__`` on every class in the chain is what
keeps the single-object case readable.
"""

from __future__ import annotations

import html
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import TYPE_CHECKING

import logfire
from constance import config
from django.core.exceptions import SuspiciousOperation
from django.db.models import Count, Max, Q
from django.urls import reverse
from django.utils import timezone
from django_countries.fields import Country

from apps.accounts.models import GuildMember, User
from apps.accounts.utils import resolve_country
from apps.rider_data.models import RiderProfile
from apps.rider_data.services import zwids_to_refresh
from apps.team.kits import current_kit

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
# first_name/last_name are here for the SEARCH INDEX only -- members look each other up by
# real name, and both the v1 roster and the Discord bot's teammate search already allow it.
# They are never put on a card: searchable and displayable are different permissions.
ACCOUNT_COLUMNS = (
    "id",
    "zwid",
    "first_name",
    "last_name",
    "zwid_verified",
    "zwid_verification_method",
    "is_race_ready",
    "is_extra_verified",
    "discord_username",
    "discord_id",
    "discord_avatar",
    "team_kit",
)

GUILD_COLUMNS = ("user_id", "joined_at", "nickname", "display_name", "username")

# The same table read from the other side: members with no user row at all, who are invisible
# to GUILD_COLUMNS' consumer because it joins on user_id. discord_id is here for the same
# reason it is in ACCOUNT_COLUMNS -- the avatar URL contains the snowflake, so showing an
# avatar is already publishing it -- and avatar_hash because GuildMember.avatar_url is a model
# property, and this module works in .values() dicts by design.
GUILD_GAP_COLUMNS = ("discord_id", "username", "display_name", "nickname", "avatar_hash", "joined_at")

# An ALLOW-list, so a bracket zauth invents later is hidden until somebody decides to show it.
# A deny-list fails the other way. The real vocabulary upstream is
# Snr / Jnr / U23 / Vet / Mas / 50+ / 60+ / 70+ / "-" / "", so what this hides is "-" and "".
#
# Jnr is SHOWN because Vincent said so ("Don't hide Jnr", 2026-09-11), overruling the plan,
# which had proposed hiding it so a minor is not marked out to the team. Recorded because the
# question is worth re-asking if the roster ever leaves team_member.
AGE_BRACKETS_ORDER = ("Jnr", "U23", "Snr", "Vet", "Mas", "50+", "60+", "70+")
AGE_BRACKETS_SHOWN = frozenset(AGE_BRACKETS_ORDER)

# Duration keys on the zauth power curves, in SECONDS.
_20_MINUTES = "1200"
_1_MINUTE = "60"


# Bracketed club tags: "Ada R [COALITION]", "Ada R (COALITION)". Roughly half of all names
# carry one, and 643 of them literally say COALITION -- members search by club, so the
# stripped form is an ADDITIONAL haystack entry, never a replacement.
_CLUB_TAG = re.compile(r"[\[(][^\])]*[\])]")


def fold(text: str) -> str:
    """Reduce a name to the form both a query and a stored name are compared in.

    Done in Python rather than with ``icontains`` because the database does not agree with
    itself: SQLite's LIKE folds ASCII only (measured: ``'Ä' LIKE '%ä%'`` is false) while
    Postgres wraps both sides in a locale-aware UPPER(). 156 names in the dev copy are
    non-ASCII, so a case-insensitivity test could pass locally and behave differently on
    Railway. It also lets the fold do things no lookup can: unescape the entities
    ZwiftRacing stores raw, and strip the accents nobody types.

    Args:
        text: A stored name or a typed query.

    Returns:
        Unescaped, accent-stripped, case-folded, whitespace-collapsed text.

    """
    if not text:
        return ""
    plain = unicodedata.normalize("NFKD", html.unescape(text))
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return " ".join(plain.casefold().split())


def without_club_tag(text: str) -> str:
    """Return the name with any bracketed club tag removed.

    Args:
        text: A rider name.

    Returns:
        The name without its tag, or "" if the tag was the whole name.

    """
    return " ".join(_CLUB_TAG.sub(" ", text).split())


def as_zwid(query: str) -> int | None:
    """Parse a query that is entirely digits, the strict way.

    ``int()`` alone accepts "1_2", "+12" and full-width digits, and ``str.isdigit()`` is true
    for non-ASCII digits, so both are checked.

    Args:
        query: The raw search text.

    Returns:
        The zwid, or None when the query is not a plain run of ASCII digits.

    """
    text = query.strip()
    return int(text) if text.isascii() and text.isdigit() else None


# Highest to lowest. Sorting these alphabetically is a live bug on the roster this
# replaces (Amethyst, Bronze, Copper...), which puts the top tier sixth.
ZR_CATEGORY_ORDER = (
    "Diamond", "Ruby", "Emerald", "Sapphire", "Amethyst",
    "Platinum", "Gold", "Silver", "Bronze", "Copper",
)
CATEGORY_ORDER = ("A+", "A", "B", "C", "D", "E")

# Upstream says "M"/"F"; zauth has passed through "male"/"female" as well. Both are read,
# and ANYTHING else is unknown -- never quietly counted as men, which is what the roster
# this replaces does in four separate places.
_WOMEN = frozenset({"f", "female", "w", "women"})
_MEN = frozenset({"m", "male", "men"})

# Coarse on purpose. Fine-grained power steps narrow the weight recoverable from
# zFTP over W/kg, and nobody browses a roster by the watt.
# How far back the card's counts look. Ninety days is wide enough to catch a rider between
# blocks without reaching back to a season that no longer says anything about their form.
#
# The number lives ONLY here. Nothing downstream repeats it -- not a field name, not a tile
# label, not a sort label -- because a field called races_30d holding ninety days of racing
# is a lie that reads as documentation.
RACE_WINDOW_DAYS = 90

WKG_STEPS = (2.5, 3.0, 3.5, 4.0, 4.5, 5.0)
FTP_STEPS = (150, 200, 250, 300, 350, 400)
JOINED_WINDOWS = (30, 90)


def gender_bucket(raw: str) -> str:
    """Bucket a stored gender into the three states the roster actually has.

    Args:
        raw: The stored value, in whatever spelling upstream used.

    Returns:
        "women", "men", or "unknown".

    """
    value = (raw or "").strip().casefold()
    if value in _WOMEN:
        return "women"
    if value in _MEN:
        return "men"
    return "unknown"


@dataclass(frozen=True, slots=True)
class RiderCard:
    """What the page may know about one rider's racing.

    Every field here is renderable, the zwid included: it is Zwift's public id for a rider,
    the old roster prints it for everyone, and the search box above these cards takes one.
    What stays out is the measurement -- weight, height, birth year -- and the allow-list is
    what keeps it out.

    Attributes:
        zwid: Zwift id. Also the join key, and an exact search term.
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

    zwid: int
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
    races_recent: int = 0
    time_trials_recent: int = 0
    rides_recent: int = 0
    podiums_recent: int = 0
    wins_recent: int = 0

    @property
    def competitive_recent(self) -> int:
        """Races and time trials in the window, which is what the roster ranks on.

        Returns:
            Competitive starts; group rides are counted separately and never ranked.

        """
        return self.races_recent + self.time_trials_recent

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
        kit_status: The rider's stored status for the CURRENT kit, or "" when the team has
            not asked them yet. Only riders with an account can have one, which is most of
            why it lives on this half rather than on the card.
        kit_label: That status in the team's own words.
        kit_badge: The DaisyUI class the kit page already uses for it, so one status does
            not look like two different things in two places.
        kit_icon_url: The CURRENT kit's own uploaded artwork, and only for a rider whose kit
            is settled -- a jersey against "Needs kit" would say the opposite of the words
            beside it. Blank falls the card back to the site-wide icon and then to the
            bundled one, so this is an override rather than the only source.
        events: Upcoming events this rider has signed up for and the reader may know about --
            see ``event_chips`` for which those are.

    """

    user_id: int
    discord_name: str = ""
    avatar_url: str = ""
    is_race_ready: bool = False
    is_extra_verified: bool = False
    member_since: datetime | None = None
    kit_status: str = ""
    kit_label: str = ""
    kit_badge: str = ""
    kit_icon_url: str = ""
    events: tuple[EventChip, ...] = ()

    def __str__(self) -> str:
        """Return the Discord name, so rendering the object never falls back to its repr.

        Returns:
            The rider's Discord name.

        """
        return self.discord_name


@dataclass(frozen=True, slots=True)
class RosterRow:
    """One card, with its account half when the rider has proved the zwid is theirs.

    Attributes:
        card: What may be shown.
        account: The rider's account half, or None when no verified claim resolved.
        _search: ``(folded, as written)`` for every name this rider is known by. Server-side
            only, and ``repr=False`` because it holds real names -- a rider's legal name and
            their Discord handle among them -- which are searchable but never displayed. The
            first entry is always the card's own name, which is how ``matched_as`` stays
            empty for the ordinary case.
        matched_as: The name that matched the query, when it was NOT the name on the card --
            "matched: Ada R [COALITION]". Empty otherwise.

    """

    card: RiderCard
    account: AccountFacts | None = None
    _search: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    matched_as: str = ""

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
        team_size: How many riders count as racing for the team at all -- the population the
            cards are drawn FROM. A card needs a cached ``RiderProfile`` as well, so this is
            always >= the number of rows, and the difference is riders nothing has fetched
            stats for yet. The page states both, because a roster quietly showing a third of
            the team looks like a team that shrank.

    """

    rows: tuple[RosterRow, ...] = ()
    synced_at: datetime | None = None
    joined_count: int = 0
    contested_count: int = 0
    team_size: int = 0

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

    @property
    def unstatted_count(self) -> int:
        """Riders on the team that nothing has fetched stats for, so they have no card.

        Returns:
            How many of the team are missing from the page.

        """
        return max(self.team_size - len(self.rows), 0)


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


def _card(row: dict, payload: dict, record: RaceRecord | None = None) -> RiderCard:
    """Build one card from an allow-listed row, its (discarded) payload and its results.

    Args:
        row: A ``.values(*CARD_COLUMNS)`` row, with ``payload`` already removed.
        payload: The ProfileFull document, read here and not retained.
        record: The rider's recent racing, or None when we hold no results for them.

    Returns:
        The rider's card.

    """
    record = record or RaceRecord()
    totals = _block(payload, "totals")
    metres = _number(totals.get("distance_km"))  # MISNAMED upstream: the value is metres.

    return RiderCard(
        zwid=row["zwid"],
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
        # Our own results first: they are refreshed on a schedule, while the cached profile's
        # date only moves when somebody presses Update. The cached one is still the fallback,
        # because results exist for well under half the roster and a date we hold beats none.
        last_race_at=record.last_result_at or row["last_race_at"],
        races_recent=record.races,
        time_trials_recent=record.time_trials,
        rides_recent=record.rides,
        podiums_recent=record.podiums,
        wins_recent=record.wins,
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


def _zwift_names(roster_zwids: list[int]) -> dict[int, list[str]]:
    """Collect the in-game names the cache does not keep, keyed by zwid.

    zauth stores one merged name -- ``_first(Zwift, ZwiftPower, ZwiftRacing)`` -- so the
    losing spelling is gone from ``RiderProfile``. A rider findable today by their
    ZwiftRacing name would stop being findable if the index read the cache alone, and the
    two columns really do disagree for hundreds of riders. Two cheap ``values_list`` reads,
    measured at under 2 ms for 3,000 names.

    Args:
        roster_zwids: The riders on the roster.

    Returns:
        Extra names per zwid, in no particular order.

    """
    from apps.zwiftpower.models import ZPTeamRiders
    from apps.zwiftracing.models import ZRRider

    names: dict[int, list[str]] = {}
    for model in (ZPTeamRiders, ZRRider):
        for zwid, name in model.objects.filter(zwid__in=roster_zwids).values_list("zwid", "name"):
            if name:
                names.setdefault(zwid, []).append(name)
    return names


def _haystack(
    card: RiderCard, account: dict | None, guild: dict | None, extra: list[str]
) -> tuple[tuple[str, str], ...]:
    """Build every name this rider can be found by, folded, the card's own name first.

    The account's names are only ever passed in for a card that JOINED that account. A
    search for "Bob Smith" returning a card headed with somebody else's ZwiftPower name
    would assert exactly the link the verification rule refuses to assert -- made through
    the search box instead of the card body.

    Real names go in here and never onto the card: findable and displayed are different
    permissions, and this is the one place that distinction is enforced.

    Args:
        card: The rider's card, whose name leads the list.
        account: The joined account's row, or None.
        guild: That account's open guild membership, or None.
        extra: In-game names from the per-source tables.

    Returns:
        ``(folded, as written)`` pairs, de-duplicated, card name first.

    """
    written = [card.name, *extra]
    if account:
        guild = guild or {}
        written += [
            guild.get("nickname") or "",
            guild.get("display_name") or "",
            guild.get("username") or "",
            account.get("discord_username") or "",
            account.get("first_name") or "",
            account.get("last_name") or "",
            f"{account.get('first_name') or ''} {account.get('last_name') or ''}",
        ]

    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in written:
        for candidate in (name, without_club_tag(name)):
            folded = fold(candidate)
            if folded and folded not in seen:
                seen.add(folded)
                pairs.append((folded, name.strip()))
    return tuple(pairs)


def search(rows: tuple[RosterRow, ...], query: str) -> list[RosterRow]:
    """Narrow the roster to the riders matching a typed query.

    A run of digits is matched against the zwid EXACTLY, and OR-ed with the name search
    rather than replacing it. Substring-matching the zwid -- what the roster this replaces
    does -- turns the box into an oracle that narrows a rider's id a digit at a time, while
    an exact match can only confirm a number the searcher already holds. The OR matters the
    other way too: 110 names contain digits, so "202" has to keep finding "Team 202".

    Args:
        rows: The whole roster.
        query: Raw text from the search box.

    Returns:
        The matching rows, each carrying ``matched_as`` when the hit was on a name the card
        does not show.

    """
    folded = fold(query)
    wanted_zwid = as_zwid(query)
    if not folded and wanted_zwid is None:
        return list(rows)

    hits: list[RosterRow] = []
    for row in rows:
        if wanted_zwid is not None and row.card.zwid == wanted_zwid:
            hits.append(row)
            continue
        if not folded:
            continue
        matched = next((written for name, written in row._search if folded in name), None)
        if matched is None:
            continue
        # The first haystack entry is the card's own name, so "matched:" only appears when
        # the rider was found by something the card does not show.
        hits.append(row if matched == row.card.name else replace(row, matched_as=matched))
    return hits


# Sorts. Each is (label, key). A key returning None means "no figure", which always sorts
# LAST regardless of direction -- a descending sort that leads with every rider we know
# nothing about is the opposite of what the reader asked for.
SORTS = {
    "races": (f"Team races ({RACE_WINDOW_DAYS} days)", lambda row: row.card.competitive_recent),
    "podiums": (f"Podiums ({RACE_WINDOW_DAYS} days)", lambda row: row.card.podiums_recent),
    "name": ("Name", lambda row: row.card.name.casefold()),
    "velo": ("vELO", lambda row: row.card.velo),
    "ftp": ("FTP", lambda row: row.card.zftp),
    "wkg": ("20-minute W/kg", lambda row: row.card.wkg_20min),
    "last_raced": ("Last raced", lambda row: row.card.last_race_at),
    "newest": ("Newest member", lambda row: row.account.member_since if row.account else None),
    "longest": ("Longest serving", lambda row: row.account.member_since if row.account else None),
}
# The page opens on the riders who are racing, which is the point of it.
DEFAULT_SORT = "races"
_DESCENDING_BY_DEFAULT = frozenset({"races", "podiums", "velo", "ftp", "wkg", "last_raced", "newest"})


# The three ways a person on this team can have no card. One filter, three values: they are
# three states of one question, not three questions.
LINK_VALUES = ("no_account", "no_zwid", "no_stats")
# The list behind "No stats yet" is narrower than the phrase sounds: riders with an account
# HERE and no cached profile, not every rider on the team the cache is behind on. The header
# used to state that wider number two lines above, which is why this option was once worded
# "Members, no stats"; that sentence is gone, so the plain wording reads correctly again. The
# heading on the list itself is what says which population it means.
LINK_LABELS = {
    "no_account": "No account here",
    "no_zwid": "No Zwift ID",
    "no_stats": "No stats yet",
}
# What to call them in a sentence, singular and plural. A no_account row is not a rider -- we
# have no idea whether they ride -- and calling them one in the copy is how a worklist starts
# overclaiming. Both forms because these lists reach one deliberately: the last person who has
# not signed in is exactly when someone reads the sentence closely.
LINK_NOUNS = {
    "no_account": ("person", "people"),
    "no_zwid": ("member", "members"),
    "no_stats": ("rider", "riders"),
}


@dataclass(frozen=True, slots=True)
class LinkRow:
    """One person who has no card, and enough to go and ask them about it.

    Deliberately thinner than ``RosterRow``: these people have no stats by definition, so
    there is nothing to rank them by and no reason to carry anything that could be. The same
    two rules apply -- a frozen slots class with a pinned field set, and ``_search``
    carrying real names under both an underscore and ``repr=False``.

    Attributes:
        name: What to head the row with.
        discord_id: The snowflake, for the DM link. Empty when we hold no Discord row.
        discord_handle: The @name under the title, when it differs from ``name``.
        avatar_url: Built here rather than read off the model, since this module works in
            ``.values()`` dicts.
        user_id: Set only when an account here resolved, which is what decides whether a
            profile link is offered.
        zwid: Set only for ``no_stats``; the other two populations have none by definition.
        joined_at: When they joined the Discord. Nullable upstream and often absent.
        _search: ``(folded, as written)`` per name, server-side only.
        matched_as: The name that matched, when it was not the one shown.

    """

    name: str
    discord_id: str = ""
    discord_handle: str = ""
    avatar_url: str = ""
    user_id: int | None = None
    zwid: int | None = None
    joined_at: datetime | None = None
    _search: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    matched_as: str = ""

    def __str__(self) -> str:
        """Return the person's name, never the row's repr.

        Returns:
            The name.

        """
        return self.name


def _no_account_qs():
    """Guild members with no account here.

    Every clause is load-bearing. ``user__isnull`` is the population; ``date_left__isnull``
    keeps people who have left out of a list of people to chase; ``is_bot`` keeps the bots
    out. The ``discord_id`` exclusion is the subtle one: the guild sync runs every six hours,
    so a member who signed in ten minutes ago still has ``user=None`` on their cached row, and
    without it this list accuses them of not having done the thing they just did.

    Returns:
        The queryset.

    """
    linked = User.objects.exclude(discord_id="").exclude(discord_id__isnull=True).values("discord_id")
    return GuildMember.objects.filter(
        user__isnull=True, date_left__isnull=True, is_bot=False
    ).exclude(discord_id__in=linked)


def _no_zwid_qs():
    """Members who signed in but never connected Zwift.

    ``zwid=0`` matters as much as ``zwid IS NULL``: 0 is the "no zwid" sentinel the roster
    index already excludes, so a query testing only for null silently misses everyone stored
    the other way. The guild-membership clause is what keeps the list to current members --
    without it the first row is the bootstrap superuser, which has no Discord at all.

    Returns:
        The queryset.

    """
    return User.objects.filter(is_active=True).filter(Q(zwid__isnull=True) | Q(zwid=0)).filter(
        guild_member__isnull=False, guild_member__date_left__isnull=True, guild_member__is_bot=False
    )


def _no_stats_qs():
    """Our riders with a zwid that no cached profile exists for.

    Scoped to people with an account HERE on purpose. The roster is built from a union that
    includes the whole ZwiftPower team page, so the unscoped version of this question is
    dominated by riders who never registered and have no relationship to the app -- a list
    nobody can act on. ``RosterIndex.unstatted_count`` still states that wider number, which
    is a different fact and is worded as one.

    Returns:
        The queryset.

    """
    return User.objects.filter(is_active=True, zwid__isnull=False, zwid__gt=0).filter(
        guild_member__isnull=False, guild_member__date_left__isnull=True, guild_member__is_bot=False
    ).exclude(zwid__in=RiderProfile.objects.values("zwid"))


def _link_row(*, name, discord_id="", handle="", avatar_hash="", user_id=None, zwid=None,
              joined_at=None, extra_names=()):
    """Assemble one row, folding every name it is known by into the haystack.

    Args:
        name: The name to head the row with.
        discord_id: The Discord snowflake, when we hold one.
        handle: The Discord @name, shown only when it differs from ``name``.
        avatar_hash: Discord's avatar hash, for the CDN URL.
        user_id: The account here, when one resolved.
        zwid: The rider's Zwift id, for the ``no_stats`` population.
        joined_at: When they joined the Discord.
        extra_names: Other names they are known by -- searchable, never shown.

    Returns:
        The row.

    """
    avatar = ""
    if discord_id and avatar_hash:
        avatar = f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.png"
    # The shown name leads, so matched_as stays empty for the ordinary case -- the same rule
    # the card's haystack follows.
    names = [name, *(n for n in extra_names if n)]
    return LinkRow(
        name=name,
        discord_id=str(discord_id or ""),
        discord_handle=handle if handle and handle != name else "",
        avatar_url=avatar,
        user_id=user_id,
        zwid=zwid,
        joined_at=joined_at,
        _search=tuple((fold(n), n) for n in names if n),
    )


def build_link_rows(kind: str) -> list[LinkRow]:
    """Build the list for one of the three populations, ordered by folded name.

    Name order rather than join date, because the join date is nullable upstream and mostly
    absent, so ordering on it puts most of the list in an arbitrary bucket. The sort control
    reorders from here, and relies on arriving sorted for its own missing bucket.

    Args:
        kind: One of ``LINK_VALUES``.

    Returns:
        The rows, or an empty list for an unrecognised kind.

    """
    rows: list[LinkRow] = []
    if kind == "no_account":
        for row in _no_account_qs().values(*GUILD_GAP_COLUMNS).iterator(chunk_size=500):
            shown = row["nickname"] or row["display_name"] or row["username"] or "Unknown member"
            rows.append(_link_row(
                name=shown,
                discord_id=row["discord_id"],
                handle=row["username"] or "",
                avatar_hash=row["avatar_hash"] or "",
                joined_at=row["joined_at"],
                extra_names=(row["display_name"], row["username"], row["nickname"]),
            ))
    elif kind in ("no_zwid", "no_stats"):
        qs = _no_zwid_qs() if kind == "no_zwid" else _no_stats_qs()
        columns = (
            "id", "zwid", "first_name", "last_name", "discord_id", "discord_avatar",
            "discord_username", "guild_member__nickname", "guild_member__display_name",
            "guild_member__username", "guild_member__joined_at",
        )
        for row in qs.values(*columns).iterator(chunk_size=500):
            shown = (
                row["guild_member__nickname"]
                or row["guild_member__display_name"]
                or row["guild_member__username"]
                or row["discord_username"]
                or "Unknown member"
            )
            real = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
            rows.append(_link_row(
                name=shown,
                discord_id=row["discord_id"] or "",
                handle=row["discord_username"] or "",
                avatar_hash=row["discord_avatar"] or "",
                user_id=row["id"],
                zwid=row["zwid"] if kind == "no_stats" else None,
                joined_at=row["guild_member__joined_at"],
                # Real names are searchable and never shown, exactly as on a card.
                extra_names=(real, row["discord_username"], row["guild_member__display_name"]),
            ))
    rows.sort(key=lambda r: (fold(r.name), r.discord_id))
    return rows


def search_link_rows(rows: list[LinkRow], query: str) -> list[LinkRow]:
    """Narrow a link list by a typed query, on the same terms as the card search.

    A digit run matches the zwid exactly and is OR-ed with the name search, for the reason
    :func:`search` gives: substring-matching an id turns the box into an oracle.

    Args:
        rows: The population.
        query: Raw text from the search box.

    Returns:
        The matching rows, carrying ``matched_as`` when the hit was on a hidden name.

    """
    folded = fold(query)
    wanted_zwid = as_zwid(query)
    if not folded and wanted_zwid is None:
        return list(rows)

    hits: list[LinkRow] = []
    for row in rows:
        if wanted_zwid is not None and row.zwid == wanted_zwid:
            hits.append(row)
            continue
        if not folded:
            continue
        matched = next((written for name, written in row._search if folded in name), None)
        if matched is None:
            continue
        hits.append(row if matched == row.name else replace(row, matched_as=matched))
    return hits


def sort_link_rows(rows: list[LinkRow], direction: str) -> list[LinkRow]:
    """Order a link list by join date, keeping rows with no date last either way.

    ``GuildMember.joined_at`` is nullable and the sync leaves it None whenever Discord's
    timestamp will not parse, so a plain sort would lead a descending list with everyone we
    hold no date for. Rows arrive name-sorted, which is what orders that bucket.

    Args:
        rows: The population.
        direction: "asc" for oldest first; anything else is newest first.

    Returns:
        The ordered rows.

    """
    present = [r for r in rows if r.joined_at is not None]
    missing = [r for r in rows if r.joined_at is None]
    present.sort(key=lambda r: r.joined_at, reverse=direction != "asc")
    return present + missing


@dataclass(frozen=True, slots=True)
class RosterFilters:
    """What the reader asked the roster to narrow to.

    Every field is a plain string straight off the querystring, validated on the way in, so
    an unknown or hand-edited value narrows nothing rather than raising.

    Attributes:
        category: A ZwiftPower category, matched against the open OR the women's field.
        zr: A Zwift Racing tier.
        gender: "women", "men" or "unknown" -- three states, never two.
        phenotype: A phenotype label.
        verified: "verified" or "extra".
        age: A bracket label, from the shown set only.
        account: "yes" for riders with an account here, "no" for the rest.
        wkg: Minimum 20-minute W/kg.
        ftp: Minimum zFTP in watts.
        joined: Days since joining the Discord, 30 or 90.
        racing: "30" or "90" for a rider who raced that recently, "quiet" for one who has
            not raced in 60 days -- including one who has never raced at all.
        country: An ISO 3166-1 alpha-2 code. Subdivisions resolve to their parent, so
            picking "United Kingdom" finds the Welsh and Scottish riders too -- which is
            what the card promises, since it flies the Union Flag for all of them.
        link: One of ``LINK_VALUES``. Unlike every other field here it does not narrow the
            roster -- it selects a different list entirely, of people who have no card at
            all. Setting it clears the rest, because a stat filter over people with no
            stats returns nothing and reads as a broken page.

    """

    category: str = ""
    zr: str = ""
    gender: str = ""
    phenotype: str = ""
    verified: str = ""
    age: str = ""
    account: str = ""
    wkg: float | None = None
    ftp: int | None = None
    joined: int | None = None
    racing: str = ""
    country: str = ""
    link: str = ""

    @property
    def active(self) -> bool:
        """Whether anything is being narrowed.

        Returns:
            True if any filter is set.

        """
        return any(value not in ("", None) for value in (
            self.category, self.zr, self.gender, self.phenotype,
            self.verified, self.age, self.account, self.wkg, self.ftp, self.joined, self.racing,
            self.country, self.link,
        ))


def _one_of(params, key: str, allowed) -> str:
    """Read a querystring value only if it is one we offer.

    Args:
        params: The request's GET parameters.
        key: The parameter name.
        allowed: The values this parameter may take.

    Returns:
        The value, or "" when absent or unrecognised.

    """
    value = (params.get(key) or "").strip()
    return value if value in allowed else ""


def _number_choice(params, key: str, allowed):
    """Read a numeric querystring value, restricted to the steps offered.

    Args:
        params: The request's GET parameters.
        key: The parameter name.
        allowed: The permitted numbers.

    Returns:
        The number, or None.

    """
    raw = (params.get(key) or "").strip()
    for step in allowed:
        if raw == str(step):
            return step
    return None


def parse_filters(params, rows: tuple[RosterRow, ...]) -> RosterFilters:
    """Read the filter state out of a querystring.

    Values are checked against what this roster actually offers -- the categories present,
    the phenotypes present -- rather than against a fixed list, so an option that no rider
    has cannot be selected and a typo narrows nothing instead of returning an empty page
    the reader cannot explain.

    Args:
        params: The request's GET parameters.
        rows: The whole roster, for the vocabularies it actually contains.

    Returns:
        The filters to apply.

    """
    # Rule made structural rather than remembered: on the not-linked lists every stat field
    # is blank by construction, so a hand-edited ?wkg=4.5&link=no_stats narrows nothing, the
    # panel's selects fall back to "Any", and apply_filters is never reached at all.
    link = _one_of(params, "link", LINK_VALUES)
    if link:
        return RosterFilters(link=link)

    options = filter_options(rows)
    return RosterFilters(
        category=_one_of(params, "category", options["categories"]),
        zr=_one_of(params, "zr", options["zr"]),
        gender=_one_of(params, "gender", ("women", "men", "unknown")),
        phenotype=_one_of(params, "phenotype", options["phenotypes"]),
        verified=_one_of(params, "verified", ("verified", "extra")),
        age=_one_of(params, "age", options["ages"]),
        account=_one_of(params, "account", ("yes", "no")),
        wkg=_number_choice(params, "wkg", WKG_STEPS),
        ftp=_number_choice(params, "ftp", FTP_STEPS),
        joined=_number_choice(params, "joined", JOINED_WINDOWS),
        racing=_one_of(params, "racing", ("30", "90", "quiet")),
        country=_one_of(params, "country", [code for code, _ in options["countries"]]),
    )


def filter_options(rows: tuple[RosterRow, ...]) -> dict[str, list]:
    """Collect the values the roster actually holds, in a sensible order.

    Args:
        rows: The whole roster.

    Returns:
        The choices for each dropdown.

    """
    categories = {row.card.category_open for row in rows} | {row.card.category_women for row in rows}
    zr = {row.card.category_racing for row in rows}
    phenotypes = {row.card.phenotype for row in rows}
    ages = {row.card.age_bracket for row in rows}
    # Keyed by the resolved code so the four UK subdivisions collapse into one option, and
    # labelled with the country name because nobody scans a roster for "GB-WLS".
    countries: dict[str, str] = {}
    for row in rows:
        code, _ = resolve_country(row.card.country)
        if code and code not in countries:
            countries[code] = Country(code).name
    return {
        "categories": [c for c in CATEGORY_ORDER if c in categories],
        "zr": [c for c in ZR_CATEGORY_ORDER if c in zr],
        "phenotypes": sorted(p for p in phenotypes if p),
        "ages": [a for a in AGE_BRACKETS_ORDER if a in ages],
        "countries": sorted(countries.items(), key=lambda pair: pair[1]),
    }


def apply_filters(rows: list[RosterRow], filters: RosterFilters) -> list[RosterRow]:
    """Narrow the roster to the riders the reader asked for.

    Args:
        rows: The rows to narrow.
        filters: The filter state.

    Returns:
        The matching rows, order preserved.

    """
    now = timezone.now()
    cutoff = now - timedelta(days=filters.joined) if filters.joined else None

    def keep(row: RosterRow) -> bool:
        card, account = row.card, row.account
        if filters.category and filters.category not in (card.category_open, card.category_women):
            return False
        if filters.zr and card.category_racing != filters.zr:
            return False
        if filters.gender and gender_bucket(card.gender) != filters.gender:
            return False
        if filters.phenotype and card.phenotype != filters.phenotype:
            return False
        if filters.age and card.age_bracket != filters.age:
            return False
        if filters.verified == "verified" and not (account and account.is_race_ready):
            return False
        if filters.verified == "extra" and not (account and account.is_extra_verified):
            return False
        if filters.account == "yes" and account is None:
            return False
        if filters.account == "no" and account is not None:
            return False
        # A missing figure is not a small one: a rider we have no W/kg for must not be
        # swept up by "at least 2.5", which would assert a measurement we do not hold.
        if filters.wkg is not None and (card.wkg_20min is None or card.wkg_20min < filters.wkg):
            return False
        if filters.ftp is not None and (card.zftp is None or card.zftp < filters.ftp):
            return False
        if filters.racing and not _raced(card, filters.racing, now):
            return False
        if filters.country and resolve_country(card.country)[0] != filters.country:
            return False
        return not (cutoff and not (account and account.member_since and account.member_since >= cutoff))

    return [row for row in rows if keep(row)]


# "Quiet" is deliberately longer than the windows above it: a rider is only worth flagging
# as inactive once they have been missing for longer than a normal break between blocks.
QUIET_DAYS = 60


def _raced(card: RiderCard, window: str, now: datetime) -> bool:
    """Whether a card satisfies a race-recency filter.

    A rider we hold no race date for counts as quiet, not as excluded: "no race on record"
    and "has not raced lately" are the same answer to the reader's question, and treating
    absence as a third state would hide exactly the riders the filter is looking for.

    Args:
        card: The rider's card.
        window: "30", "90" or "quiet".
        now: The moment to measure from.

    Returns:
        Whether the rider matches.

    """
    last = card.last_race_at
    if window == "quiet":
        return last is None or last < now - timedelta(days=QUIET_DAYS)
    return last is not None and last >= now - timedelta(days=int(window))


def sort_rows(rows: list[RosterRow], sort: str, direction: str) -> list[RosterRow]:
    """Order the roster, keeping riders with no figure at the end either way.

    Args:
        rows: The rows to order, already in name order.
        sort: A key from ``SORTS``.
        direction: "asc" or "desc".

    Returns:
        The ordered rows.

    """
    if sort not in SORTS:
        sort = DEFAULT_SORT
    key = SORTS[sort][1]
    descending = direction == "desc" if direction in ("asc", "desc") else sort in _DESCENDING_BY_DEFAULT

    present = [row for row in rows if key(row) is not None]
    missing = [row for row in rows if key(row) is None]
    # Stable, and the rows arrive in name order, so equal values stay alphabetical.
    present.sort(key=key, reverse=descending)
    return present + missing


# ZwiftPower's f_t is a SPACE-SEPARATED SET OF FLAGS, not a type: "TYPE_RACE TYPE_WOMENS",
# "TYPE_TEAM_TIME_TRIAL TYPE_RACE". Three consequences, each of which is a live bug
# somewhere in this app if you get it wrong:
#
# * Match by CONTAINS, never by equality. `f_t="TYPE_RACE"` drops all 576 women's races,
#   which is what apps/zwiftpower/views.py does today.
# * "TIME_TRIAL", not "TYPE_TIME_TRIAL", because the flag for a team TT is
#   TYPE_TEAM_TIME_TRIAL and the longer needle does not appear inside it.
# * The flags overlap, so the order below is a decision. A team time trial is flagged
#   TYPE_TEAM_TIME_TRIAL *and* TYPE_RACE; counting it under both would inflate a rider's
#   race count with their own time trials. Time trial wins, race beats ride, and workouts
#   and runs are neither.
_TT_FLAG = "TIME_TRIAL"
_RACE_FLAG = "TYPE_RACE"
_RIDE_FLAG = "TYPE_RIDE"

# A podium means a podium in the rider's own category, which is what riders claim and what
# ZwiftPower shows them. Overall position across every category is close to meaningless in a
# mixed field, and would flatter riders in the fastest one.
_PODIUM = 3


@dataclass(frozen=True, slots=True)
class RaceRecord:
    """One rider's racing in the recent window, from our own results table.

    Attributes:
        races: Races, excluding time trials and group rides.
        time_trials: Individual and team time trials.
        rides: Group rides. Counted and shown, never ranked -- Vincent's rule is that a
            group ride is not racing.
        podiums: Top-three finishes in the rider's own category, races and TTs only.
        wins: Category wins, a subset of podiums.
        last_result_at: The rider's most recent result of any kind, ALL TIME rather than
            within the window -- otherwise a rider who has not raced for two months would
            read as never having raced at all.

    """

    races: int = 0
    time_trials: int = 0
    rides: int = 0
    podiums: int = 0
    wins: int = 0
    last_result_at: datetime | None = None

    @property
    def competitive(self) -> int:
        """Races and time trials together, which is what the roster ranks on.

        Returns:
            The number of competitive starts in the window.

        """
        return self.races + self.time_trials


def race_records(roster_zwids: list[int], *, now: datetime | None = None) -> dict[int, RaceRecord]:
    """Count each rider's recent racing, in two queries flat.

    Read from ``ZPRiderResults`` rather than from the cached profile's ``last_race_at``,
    which zauth only refreshes when somebody asks it to and is stale for most riders.

    Every row in that table is already the team's own: the sync fetches this team's results,
    so "team-tagged" needs no filter here. If that ever stops being true, this is the
    function that quietly starts counting other clubs' racing.

    Args:
        roster_zwids: The riders on the roster.
        now: The moment to measure the window from; defaults to the present.

    Returns:
        One record per rider who has any result at all.

    """
    from apps.zwiftpower.models import ZPRiderResults

    now = now or timezone.now()
    cutoff = now - timedelta(days=RACE_WINDOW_DAYS)

    is_tt = Q(f_t__contains=_TT_FLAG)
    is_race = Q(f_t__contains=_RACE_FLAG) & ~is_tt
    is_ride = Q(f_t__contains=_RIDE_FLAG) & ~is_tt & ~Q(f_t__contains=_RACE_FLAG)
    podium = Q(position_in_cat__lte=_PODIUM) & Q(position_in_cat__gt=0) & (is_race | is_tt)

    # .order_by() clears Meta.ordering ("-event__event_date", "pos"), which would otherwise
    # drag those columns into the GROUP BY.
    windowed = (
        ZPRiderResults.objects.filter(zwid__in=roster_zwids, event__event_date__gte=cutoff, event__event_date__lte=now)
        .order_by()
        .values("zwid")
        .annotate(
            races=Count("pk", filter=is_race),
            time_trials=Count("pk", filter=is_tt),
            rides=Count("pk", filter=is_ride),
            podiums=Count("pk", filter=podium),
            wins=Count("pk", filter=Q(position_in_cat=1) & (is_race | is_tt)),
        )
    )
    # Last result is deliberately NOT windowed: "last raced 4 months ago" is the useful
    # answer for a rider who has been quiet, and an empty window would say nothing at all.
    latest = (
        ZPRiderResults.objects.filter(zwid__in=roster_zwids)
        .order_by()
        .values("zwid")
        .annotate(last=Max("event__event_date"))
    )

    records: dict[int, dict] = {}
    for row in windowed:
        records[row["zwid"]] = {k: v for k, v in row.items() if k != "zwid"}
    for row in latest:
        records.setdefault(row["zwid"], {})["last_result_at"] = row["last"]
    return {zwid: RaceRecord(**values) for zwid, values in records.items()}


@dataclass(frozen=True, slots=True)
class EventChip:
    """One upcoming event a rider has signed up for.

    Attributes:
        name: The event's title, which is admin-authored rather than rider-authored.
        url: Its page.
        start_date: Used only to order the chips; the card shows the name.
        role: "Captain" or "Vice-captain" when the rider leads a squad in this event, else "".
            Per event, never global -- captaincy is held on a squad, and a rider can captain
            one event's squad while just riding in another.
        logo_url: The event's own logo, when it has one. The name still travels with it: it
            is the image's accessible name, and the chip falls back to it entirely when an
            event has no logo.

    """

    name: str
    url: str
    start_date: object = None
    role: str = ""
    logo_url: str = ""

    def __str__(self) -> str:
        """Return the event name, never the repr.

        Returns:
            The event's name.

        """
        return self.name


def event_chips(user_ids: list[int], *, viewer_id: int | None = None) -> dict[int, tuple[EventChip, ...]]:
    """Find the upcoming events these riders have signed up for, in one query.

    **Who may see this is not the roster's decision to make.** An ordinary member can see who
    signed up for an event only when that event has ``show_signups`` on -- the event page
    gates its own list that way (``apps/events/views.py``: ``can_view_signups =
    can_view_signup_table or event.show_signups``), and the flag defaults to OFF. Putting
    every signup on the roster would hand every team member a list the event's own page
    deliberately withholds, for every event at once.

    So a chip appears only where all four hold:

    * the signup is REGISTERED -- a withdrawal must not linger on a card;
    * the event is ``visible``;
    * the event has not finished;
    * the event shows its signups **or** the card is the viewer's own, because nobody needs
      permission to be told what they themselves signed up for.

    Args:
        user_ids: The accounts joined to roster cards.
        viewer_id: The signed-in reader, whose own signups are always their own business.

    Returns:
        Chips per user id, soonest first.

    """
    from apps.events.models import EventSignup

    if not user_ids:
        return {}

    today = timezone.now().date()
    shown = Q(event__show_signups=True)
    if viewer_id is not None:
        shown |= Q(user_id=viewer_id)

    rows = (
        EventSignup.objects.filter(
            status=EventSignup.Status.REGISTERED,
            user_id__in=user_ids,
            event__visible=True,
            event__end_date__gte=today,
        )
        .filter(shown)
        .order_by("event__start_date", "event__title")
        .values_list("user_id", "event__id", "event__title", "event__start_date", "event__logo")
    )

    rows = list(rows)
    roles = _squad_roles({event_id for _, event_id, _, _, _ in rows})

    chips: dict[int, list[EventChip]] = {}
    for user_id, event_id, title, start_date, logo in rows:
        chips.setdefault(user_id, []).append(
            EventChip(
                name=title or "Event",
                url=reverse("events:event_detail", args=[event_id]),
                start_date=start_date,
                role=roles.get((event_id, user_id), ""),
                logo_url=_logo_url(logo),
            )
        )
    return {user_id: tuple(items) for user_id, items in chips.items()}


def _logo_url(name: str | None) -> str:
    """Turn a stored image name into a URL the card can use.

    ``.values_list()`` hands back the stored NAME, not the ``FieldFile`` a model instance
    would carry, so there is no ``.url`` to read -- which is the whole reason this exists
    rather than the template asking the object. Resolved through the field's own storage, so
    it is the bucket's URL in production and the media folder locally, exactly as a model
    instance would have produced.

    Args:
        name: The stored file name, blank or None when the event has no logo.

    Returns:
        The URL, or "" when there is no logo or the storage cannot name one.

    """
    if not name:
        return ""
    from apps.events.models import Event

    try:
        return Event._meta.get_field("logo").storage.url(name)
    except (ValueError, NotImplementedError, SuspiciousOperation):
        # A storage that refuses to name the file (an unset MEDIA_URL, a path it reads as
        # escaping the root) must cost the card its logo, not the whole roster.
        logfire.warning("Event logo could not be resolved to a URL")
        return ""


def _squad_roles(event_ids: set[int]) -> dict[tuple[int, int], str]:
    """Find who leads a squad in each of these events.

    Two queries rather than one: captains and vice-captains are separate many-to-many
    relations, and selecting both in a single ``values_list`` makes the join a cross product
    of the two, inventing pairs that do not exist.

    Only events already being chipped are asked about, so this says nothing about an event
    the reader could not otherwise see. Captaincy itself is not a secret -- squad rosters
    render to any team member on the event page -- but attaching it to an event the reader
    is not being shown would still name the event.

    Args:
        event_ids: The events whose chips are being built.

    Returns:
        ``(event_id, user_id) -> role label``, captain winning where a rider is both.

    """
    from apps.events.models import Squad

    if not event_ids:
        return {}

    roles: dict[tuple[int, int], str] = {}
    # Vice-captains first, so a rider holding both roles in one event reads as Captain.
    for relation, label in (("vice_captains", "Vice-captain"), ("captains", "Captain")):
        pairs = (
            Squad.objects.filter(event_id__in=event_ids, **{f"{relation}__isnull": False})
            .values_list("event_id", f"{relation}__id")
            .distinct()
        )
        for event_id, user_id in pairs:
            roles[event_id, user_id] = label
    return roles


def _guild_rows() -> dict[int, dict]:
    """Read current Discord memberships, keyed by user id.

    Open memberships only, so a returning member's card dates their current stint rather than
    a tenure they no longer have.

    Returns:
        One row per linked user with an open membership.

    """
    rows = GuildMember.objects.filter(user__isnull=False, date_left__isnull=True).values(*GUILD_COLUMNS)
    return {row["user_id"]: row for row in rows}


# The stored labels are written in the FIRST PERSON, for the rider's own profile: "I have
# the kit", "I need the kit". A teammate's card is read in the third person, so it needs its
# own wording -- "Kit: I have the kit" on somebody else's card reads as a mistake. The two
# Zwift-side states are already neutral and are left exactly as the kit page words them.
_KIT_CARD_LABELS = {
    "need": "Needs kit",
    "submitted": "Submitted to Zwift",
    "completed": "Completed by Zwift",
    "have": "Has the kit",
}


# The two stored statuses that mean there is nothing left to chase. The card draws a jersey
# for these and words for the rest -- see templates/team/partials/_rider_card.html. Kept
# beside the code that reads it; a test pins it against the template tag's own map so the two
# cannot drift into disagreeing about which statuses earn an icon.
SETTLED_KIT_STATUSES = frozenset({"completed", "have"})


def _kit_icon_url(kit: object | None) -> str:
    """Resolve the current kit's own artwork once, for the whole page.

    Once, not per rider: media here is served from object storage, where every URL is
    freshly SIGNED, so asking two thousand cards for the same file would mint two thousand
    signatures for one image.

    Args:
        kit: The team's current kit, or None when none is set.

    Returns:
        The icon's URL, or "" when the kit has none or storage will not name it.

    """
    icon = getattr(kit, "icon", None)
    if not icon:
        return ""
    try:
        return icon.url
    except (ValueError, NotImplementedError, SuspiciousOperation):
        # A storage that refuses to name the file costs the cards their kit icon, not the
        # roster -- the same rule the event logos follow.
        logfire.warning("Team kit icon could not be resolved to a URL")
        return ""


def _account_facts(
    account: dict,
    guild: dict | None,
    kit: object | None = None,
    events: tuple[EventChip, ...] = (),
    kit_icon_url: str = "",
) -> AccountFacts:
    """Assemble the account half of a card.

    Args:
        account: An ``ACCOUNT_COLUMNS`` row whose verification has already been accepted.
        guild: That user's open ``GuildMember`` row, if they have one.
        kit: The team's current kit, or None when none is set.
        events: That rider's visible upcoming signups.
        kit_icon_url: The current kit's artwork, already resolved by ``_kit_icon_url``.

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

    # Read straight off the JSON column rather than through kits.status_for, which takes a
    # User instance -- this half of the roster is .values() dicts by design. The default is
    # deliberately blank rather than "unknown": a rider the team has never asked has no kit
    # status to report, and a card saying "Unknown" on two thousand riders is noise, not news.
    status, label, badge = "", "", ""
    if kit is not None:
        from apps.team.kits import BADGE_CLASSES, KitStatus

        stored = (account.get("team_kit") or {}).get(kit.slug)
        if stored in KitStatus.values and stored != KitStatus.UNKNOWN:
            status = stored
            label = _KIT_CARD_LABELS[stored]
            badge = BADGE_CLASSES.get(stored, "badge-ghost")

    return AccountFacts(
        user_id=account["id"],
        discord_name=name or "",
        avatar_url=avatar,
        is_race_ready=bool(account["is_race_ready"]),
        is_extra_verified=bool(account["is_extra_verified"]),
        member_since=guild.get("joined_at"),
        kit_status=status,
        kit_label=label,
        kit_badge=badge,
        # Settled only. The icon means "nothing left to chase", so putting it beside "Needs
        # kit" would have the picture and the words saying opposite things.
        kit_icon_url=kit_icon_url if status in SETTLED_KIT_STATUSES else "",
        events=events,
    )


def build_roster_index(viewer_id: int | None = None) -> RosterIndex:
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

    Note the gate answers "has this account been verified", which is only as strong as "does
    this account own this zwid" for a zauth verification: that zwid is the one the service
    reports, and nothing else can write it now. A legacy or admin verification predates that,
    and some were left pointing at a zwid the rider later typed in by hand. Cleaning those up is
    out of this module's hands (the cutover flag stops them counting); the duplicate rule caps
    the damage at losing an account half rather than taking one over.

    Costs 16 queries, flat in the number of riders: three for the union, one Constance read
    for the cutover policy, accounts, guild memberships, the two per-source name tables, two
    for the race counts, the current kit, the event signups, two for squad captaincy, the
    cache, and the freshness stamp.

    Args:
        viewer_id: The signed-in reader. Only affects which of their OWN event signups show
            on their own card -- see ``event_chips``. Everything else is the same for
            everyone, which is what keeps the index shareable.

    Returns:
        The roster, ordered by folded name with nameless riders last.

    """
    roster_zwids = zwids_to_refresh()
    zauth_required = config.ZAUTH_VERIFICATION_REQUIRED
    claimants = _verified_claimants(zauth_required=zauth_required)
    guild_rows = _guild_rows()
    zwift_names = _zwift_names(roster_zwids)
    records = race_records(roster_zwids)
    kit = current_kit()
    kit_icon_url = _kit_icon_url(kit)
    chips = event_chips([claim["id"] for claims in claimants.values() for claim in claims], viewer_id=viewer_id)

    rows: list[RosterRow] = []
    joined = 0
    contested = 0

    # order_by() clears Meta.ordering, which would sort in SQL on a name this then re-sorts
    # in Python -- and upstream does not strip whitespace, so SQL puts " Ada" first.
    cached = RiderProfile.objects.filter(zwid__in=roster_zwids).order_by().values(*CARD_COLUMNS)
    for row in cached.iterator(chunk_size=500):
        payload = row.pop("payload") or {}
        card = _card(row, payload, records.get(row["zwid"]))

        claims = claimants.get(card.zwid, ())
        account = None
        claimed: dict | None = None
        guild: dict | None = None
        if len(claims) == 1:
            claimed = claims[0]
            guild = guild_rows.get(claimed["id"])
            account = _account_facts(
                claimed, guild, kit, chips.get(claimed["id"], ()), kit_icon_url=kit_icon_url
            )
            joined += 1
        elif len(claims) > 1:
            contested += 1
            logfire.error(
                "Roster zwid claimed by more than one verified account",
                zwid=card.zwid,  # ids only, per the logging rule
                user_ids=[claim["id"] for claim in claims],
            )
        rows.append(
            RosterRow(
                card=card,
                account=account,
                # claimed is None unless the join fired, so an unverified claimant's real and
                # Discord names never enter this rider's haystack.
                _search=_haystack(card, claimed, guild, zwift_names.get(card.zwid, [])),
            )
        )

    # Folded, so case and the whitespace upstream does not strip cannot reorder the page;
    # the zwid tiebreak keeps two riders of the same name in the same order on every request,
    # which is what stops paging showing one of them twice. Which values sort LAST is a
    # question for the sort controls, not for the index.
    rows.sort(key=lambda row: (row.card.name.casefold(), row.card.zwid))

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
        team_size=len(roster_zwids),
    )


# --- the shared index -------------------------------------------------------------------------

# How long one built index serves every reader. Building takes most of a second at ~2,000
# riders, and the page asks for it on every keystroke of a live search and every "Show more",
# so without this each of those waited the full build. A minute is short enough that a new
# signup or stat is not missed for long; nothing invalidates it on write.
ROSTER_INDEX_TTL_SECONDS = 60

_shared_index: tuple[float, RosterIndex] | None = None
_build_lock = threading.Lock()


def reset_roster_index_cache() -> None:
    """Forget the shared index, so the next request builds a fresh one. For tests."""
    global _shared_index
    with _build_lock:
        _shared_index = None


def shared_roster_index() -> RosterIndex:
    """Return the roster as everyone sees it, built at most once a minute per process.

    Kept in the process, not in Django's cache: the rows are frozen dataclasses, so readers
    can share one copy, and LocMemCache would pickle 2,000 of them on every read. One build
    at a time -- a second request arriving mid-build waits for it rather than starting its
    own.

    Returns:
        The index built with no reader, so no private event signup is on it.

    """
    global _shared_index
    cached = _shared_index
    if cached and time.monotonic() - cached[0] < ROSTER_INDEX_TTL_SECONDS:
        return cached[1]
    with _build_lock:
        cached = _shared_index
        if cached and time.monotonic() - cached[0] < ROSTER_INDEX_TTL_SECONDS:
            return cached[1]
        index = build_roster_index()
        _shared_index = (time.monotonic(), index)
        return index


def roster_index_for(viewer_id: int | None) -> RosterIndex:
    """Return the shared index with the reader's own card carrying all their event signups.

    The one part of the index that differs between readers is the reader's own card, which
    shows their signups even for events that hide them (see ``event_chips``). That card's
    chips are looked up fresh on every request, so a rider sees their own new signup at once
    however old the shared index is.

    Args:
        viewer_id: The signed-in reader.

    Returns:
        The index to render for them.

    """
    shared = shared_roster_index()
    if viewer_id is None:
        return shared
    for position, row in enumerate(shared.rows):
        if row.account is None or row.account.user_id != viewer_id:
            continue
        own = event_chips([viewer_id], viewer_id=viewer_id).get(viewer_id, ())
        if own == row.account.events:
            return shared
        rows = list(shared.rows)
        rows[position] = replace(row, account=replace(row.account, events=own))
        return replace(shared, rows=tuple(rows))
    return shared
