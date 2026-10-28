"""Cached rider profiles from the zauth unified-profile endpoints.

The zauth service merges Zwift, ZwiftPower and zwiftracing into one profile per rider and
serves it in batch. This app caches that document. It is intended to replace the local
``zwiftpower`` and ``zwiftracing`` tables, which between them carry 111 columns describing
the same riders from two directions.

**This is a cache of a remote document, not a domain model.** Every row here can be thrown
away and re-fetched. Two consequences follow, and both are load-bearing:

* Nothing that cannot be re-fetched may live on it. Roster membership in particular belongs
  in ``team``, not here -- a row carrying membership state cannot be evicted, which would
  make retention unenforceable on exactly the rows that need it.
* Fields earn a column only by being filtered, sorted, joined or gated on. Everything else
  stays in ``payload`` exactly as the service returned it. ``ZRRider`` has 81 fields because
  each was promoted the moment somebody needed the value, which is how a cache becomes a
  schema you have to migrate. The rule is the thing that stops that happening again.
"""

from typing import TYPE_CHECKING, NamedTuple

from django.db import models

from gotta_bike_platform.retention import RetentionPolicy

if TYPE_CHECKING:
    from collections.abc import Iterable


def _number(value: object) -> float | None:
    """Read a payload value as a number.

    Returns:
        The value as a float when it is a number (a bool is not), else None.

    """
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _numbers(block: dict | None, keys: dict[str, str]) -> dict | None:
    """Read ``{name: payload key}`` numbers out of a block.

    Returns:
        ``{name: value}``, None for each one missing, or None when none of them is there.

    """
    block = block if isinstance(block, dict) else {}
    found = {name: _number(block.get(key)) for name, key in keys.items()}
    return found if any(value is not None for value in found.values()) else None


# zwiftracing's six vELO 2 / seed factors, as its blocks name them.
_FACTORS = {
    "sprint": "sprint",
    "punch": "punch",
    "climb": "climb",
    "time_trial": "timeTrial",
    "endurance": "endurance",
    "pursuit": "pursuit",
}
# vELO 2's category names, as zauth's velo2 block carries them: the open category of the
# current rating and of each peak, then the women's. zwiftracing sends the women's ones for
# every rider, so whether they apply is ours to decide -- see RiderProfile.velo2_women.
_VELO2_CATEGORIES = (
    "category",
    "category_max30",
    "category_max90",
    "category_women",
    "category_women_max30",
    "category_women_max90",
)
# The same factors as the rider card names them, in zwiftracing's order.
_FACTOR_LABELS = (
    ("sprint", "Sprint"),
    ("punch", "Punch"),
    ("climb", "Climb"),
    ("time_trial", "TT"),
    ("endurance", "Endurance"),
    ("pursuit", "Pursuit"),
)
# zwiftracing's power curve durations, in seconds, with the names the pages give them.
CURVE_DURATIONS = (
    (5, "5 s"),
    (15, "15 s"),
    (30, "30 s"),
    (60, "1 min"),
    (120, "2 min"),
    (300, "5 min"),
    (1200, "20 min"),
)

# zwiftracing's tiers, highest first. vELO 1 and vELO 2 use the same names, mixed and women's
# alike. Sorting them alphabetically puts Diamond, the top tier, sixth.
ZR_TIERS = ("Diamond", "Ruby", "Emerald", "Sapphire", "Amethyst", "Platinum", "Gold", "Silver", "Bronze", "Copper")

# How the sources spell a woman: zauth passes Zwift's "F" or "female", the app's own profile
# says "female", and the roster's filters say "women".
WOMEN_GENDERS = frozenset({"f", "female", "w", "women"})


def is_woman(*genders: str | None) -> bool:
    """Whether a rider is a woman, by the first of these genders that says anything.

    Callers pass the member's own answer first (``User.gender``) and zauth's after it
    (``RiderProfile.gender``), so the rider's own word wins and zauth's fills a blank.

    Returns:
        True when the first non-blank gender is a woman's.

    """
    for gender in genders:
        value = (gender or "").strip().casefold()
        if value:
            return value in WOMEN_GENDERS
    return False


def _category_name(value: object) -> str | None:
    """Read a payload value as a category name.

    Returns:
        The value when it is a non-empty string, else None.

    """
    return value if isinstance(value, str) and value else None


class RatingRow(NamedTuple):
    """One row of a rating table on the rider card.

    Attributes:
        label: "Rating", "Mixed" or "Women's".
        cells: The current value, then the 30- and 90-day peaks', None where missing.
        numeric: Whether the cells are ratings rather than category names.

    """

    label: str
    cells: tuple[float | str | None, float | str | None, float | str | None]
    numeric: bool


def _rating_rows(ratings: tuple, mixed: tuple, women: tuple) -> list[RatingRow]:
    """Lay out one scale's ratings and categories, now and at each peak, as table rows.

    Returns:
        The rating, mixed and women's rows, each kept only when it has a value.

    """
    rows = (RatingRow("Rating", ratings, True), RatingRow("Mixed", mixed, False), RatingRow("Women's", women, False))
    return [row for row in rows if any(cell is not None for cell in row.cells)]


class RiderProfile(models.Model):
    """One rider's merged profile, as last returned by zauth.

    Attributes:
        zwid: Zwift id. The universal join key -- all three upstream sources carry it, and
            every consumer in this project joins on ``User.zwid``.
        zwift_user_id: Zwift's account UUID. Present only for riders who connected through
            zauth, so it cannot be the key; kept because the app-auth batch endpoint takes it.
        payload: The whole ``ProfileFull`` document as returned, including the blocks that
            deliberately have no column.
        fetched_at: When we last called zauth for this rider.
        last_race_at: Most recent race we know of, derived from the clubs block. The anchor
            for activity-based retention.

    """

    # --- identity -------------------------------------------------------------------
    zwid = models.BigIntegerField(primary_key=True, help_text="Zwift ID (the join key everywhere)")
    zwift_user_id = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text="Zwift account UUID; only riders connected through zauth have one",
    )

    # --- promoted: something filters, sorts or gates on these -------------------------
    # name: DB-level `name__icontains` in the roster and teammate search.
    name = models.CharField(max_length=200, blank=True, db_index=True)
    gender = models.CharField(max_length=16, blank=True, help_text="Filtered in the roster and the Sheets export")
    country = models.CharField(max_length=64, blank=True)
    age = models.CharField(max_length=16, blank=True, help_text="A bracket string upstream, not a number")

    weight_kg = models.FloatField(null=True, blank=True)
    height_cm = models.FloatField(null=True, blank=True)
    ftp = models.FloatField(null=True, blank=True)
    zftp = models.FloatField(null=True, blank=True)

    # Category gates which verifications a rider must submit, and drives Discord role
    # assignment. It is not a display field, which is why it is stored plainly.
    category_open = models.CharField(max_length=16, blank=True)
    category_women = models.CharField(max_length=16, blank=True)
    category_racing = models.CharField(max_length=16, blank=True)

    # Ratings that are filtered by range or sorted on today.
    velo = models.FloatField(null=True, blank=True, help_text="Overall racing rating")
    zwift_racing_score = models.FloatField(null=True, blank=True)
    zp_skill = models.FloatField(null=True, blank=True)
    compound_score = models.FloatField(null=True, blank=True)

    # The phenotype *value* is filtered in the Sheets export; its component scores are only
    # ever displayed, so they stay in payload. Same block, different treatment, because the
    # rule is about use rather than origin.
    phenotype_value = models.CharField(max_length=32, blank=True)

    club_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    club_name = models.CharField(max_length=200, blank=True)

    # zauth's report of whether the rider was on the latest roster fetch of our ZwiftPower
    # team (Constance ZWIFTPOWER_TEAM_ID); None when it cannot say. Every sync overwrites it,
    # so it is not the team's membership record: it must never decide whom the sync asks for
    # (a cache cannot discover riders it does not hold), and a row the sync has stopped
    # refreshing keeps its last answer. Filtered on to tell team riders from the rest.
    on_zwiftpower_team = models.BooleanField(
        null=True,
        blank=True,
        help_text=(
            "Seen on the latest ZwiftPower roster of the ZWIFTPOWER_TEAM_ID team, as zauth "
            "reports it; empty when unknown"
        ),
    )

    # --- everything else, as returned -------------------------------------------------
    payload = models.JSONField(
        default=dict,
        help_text="The full ProfileFull document: power curves, handicaps, seed, phenotype scores, known clubs",
    )

    # --- cache mechanics, not rider data ----------------------------------------------
    fetched_at = models.DateTimeField(db_index=True, help_text="When we last stored data for this rider")
    last_requested_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "When this rider was last INCLUDED IN A BATCH, whether or not data came back. "
            "Distinct from fetched_at on purpose: the difference is 'we asked and got nothing' "
            "versus 'we stopped asking', and only the second is a reason to evict"
        ),
    )
    sources = models.JSONField(default=dict, help_text="Per-source {present, fetched_at} as returned")
    has_account = models.JSONField(
        default=dict,
        help_text=(
            "Per-source booleans. False means no record stored, which is usually 'no account' but for a "
            "never-fetched source also means 'not looked up yet' -- read it with sources.*.fetched_at"
        ),
    )
    last_race_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Most recent known race, from the clubs block; the retention anchor",
    )

    retention = RetentionPolicy.delete(
        "Cached copies of a remote document, re-fetchable at any time. Most rows describe riders "
        "who never registered here -- the ZwiftPower team page, scouted opponents -- so holding "
        "their weight, power and heart rate indefinitely is the thing this app exists to bound. "
        "The window is set in Constance as RIDER_PROFILE_MAX_DAYS and DEFAULTS TO 120 DAYS; 0 "
        "disables eviction entirely. "
        "Anchored on when the rider was last REQUESTED, not when data last came back. The sync "
        "is not demand-driven -- it asks for every rider we have a reason to hold, on a "
        "schedule -- so dropping out of that set is the thing worth evicting on. Anchoring on "
        "fetched_at instead would conflate that with 'we asked and the service had nothing', "
        "which is a data problem, not a reason to delete somebody. Race activity, the "
        "obvious-looking anchor, is wrong for a third reason again: it describes the rider "
        "rather than our reason for holding them.",
        anchor="last_requested_at",
        setting="RIDER_PROFILE_MAX_DAYS",
        task="purge_rider_profiles",
    )

    class Meta:
        """Meta options for RiderProfile."""

        verbose_name = "Rider Profile"
        verbose_name_plural = "Rider Profiles"
        ordering = ("name",)

    def __str__(self) -> str:
        """Return a readable label.

        Returns:
            The rider's name and zwid.

        """
        return f"{self.name or 'Unknown'} ({self.zwid})"

    @property
    def is_stale(self) -> bool:
        """Whether this row is older than the refresh window.

        Computed rather than stored: a stored flag needs a sweep to stay true and is wrong
        between sweeps.

        Returns:
            True if the row should be refetched before being trusted.

        """
        from datetime import timedelta

        from constance import config
        from django.utils import timezone

        max_age = config.RIDER_PROFILE_REFRESH_HOURS
        if not max_age or max_age <= 0:
            return False
        return timezone.now() - self.fetched_at > timedelta(hours=max_age)

    # ------------------------------------------------------------------
    # Payload accessors
    #
    # Everything below reads the JSON document rather than a column, because of the promotion
    # rule: a field earns a column only if something filters, sorts or joins on it, and none
    # of these do -- they are display-only. The deep paths live here rather than in a template
    # so there is ONE place to fix when zauth's shape moves, and so they can be tested. Each
    # returns None (not {}) when the block is absent, so a template can gate a whole group
    # with a single {% if %}.
    # ------------------------------------------------------------------

    def _block(self, *path: str) -> dict | None:
        """Walk into ``payload`` by key, tolerating anything missing or the wrong type.

        Upstream sends ``null`` for whole blocks routinely -- a rider with no ZwiftRacing row
        has no handicaps, no phenotype and no power curve -- so absence is the normal case
        rather than an error worth raising.

        Args:
            *path: Successive dict keys.

        Returns:
            The nested dict, or None if any step is missing or is not a dict.

        """
        node = self.payload
        for key in path:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node if isinstance(node, dict) and node else None

    @property
    def has_display_data(self) -> bool:
        """Whether this row holds anything a profile card could actually show.

        A model instance is always truthy, so ``{% if rider_profile %}`` only asks whether a
        row exists. Every column here is nullable and every payload block optional, so a row
        can exist and display nothing -- and the card would then render an "Updated <date>"
        stamp over an empty body, asserting fresh data while showing none.

        ``name`` is deliberately not counted: it is a promoted column but the card never
        renders it, because the profile header already carries the rider's name.

        Zero counts as data. A 0.0 handicap or a 0 FTP is a real measurement, and testing
        truthiness rather than presence would silently discard it.

        Returns:
            True if at least one displayed field is populated.

        """
        columns = (
            self.club_name,
            self.country,
            self.age,
            self.category_open,
            self.category_women,
            self.category_racing,
            self.zwift_racing_score,
            self.weight_kg,
            self.height_cm,
            self.ftp,
            self.zftp,
            self.velo,
            self.zp_skill,
            self.compound_score,
            self.phenotype_value,
        )
        if any(value is not None and value != "" for value in columns):
            return True
        return any((
            self.power_extras,
            self.peak_ratings,
            self.handicaps,
            self.totals,
            self.velo2,
            self.seed_ratings,
            self.race_counts,
            self.power_curve_w,
            self.power_curve_wkg,
        ))

    @property
    def handicaps(self) -> dict | None:
        """Terrain handicaps: flat, rolling, hilly, mountainous.

        Note zauth FLATTENS this block -- it returns ``zr_payload["handicaps"]["profile"]``,
        so there is no ``profile`` wrapper key here and reaching for one finds nothing.

        Returns:
            The handicaps dict, or None.

        """
        return self._block("handicaps")

    @property
    def totals(self) -> dict | None:
        """Lifetime distance and elevation, from ZwiftPower.

        ``climbed_m`` is in METRES while ``distance_km`` is in kilometres; they do not share a
        unit and rendering them as if they did is the obvious mistake.

        Returns:
            The totals dict, or None.

        """
        return self._block("totals")

    @property
    def lifetime_distance_km(self) -> float | None:
        """Lifetime distance in kilometres, converted from the metres actually stored.

        The payload key is ``totals.distance_km`` but the value is METRES. zauth passes
        ZwiftPower's figure through unconverted -- ``"distance_km": zp.distance`` in
        profile_full.py -- and ZwiftPower reports metres, as our own
        ``ZPTeamRiders.distance`` help_text states. Trusting the key name rendered a rider's
        total as 37,209,725 km.

        Returns:
            Distance in kilometres, or None if absent.

        """
        block = self.totals or {}
        metres = block.get("distance_km")
        return metres / 1000 if isinstance(metres, int | float) else None

    @property
    def lifetime_climbed_m(self) -> float | None:
        """Lifetime elevation in metres.

        ``climbed_m`` IS metres, so unlike its neighbour this needs no conversion. Exposed as
        a property anyway so the template reads both totals the same way and nobody has to
        remember that exactly one of the two pair is mislabelled.

        Returns:
            Elevation in metres, or None if absent.

        """
        block = self.totals or {}
        metres = block.get("climbed_m")
        return metres if isinstance(metres, int | float) else None

    @property
    def phenotype_scores(self) -> dict | None:
        """The five per-discipline phenotype scores behind ``phenotype_value``.

        Returns:
            The scores dict (sprinter, puncheur, pursuiter, climber, tt), or None.

        """
        return self._block("phenotype", "scores")

    @property
    def phenotype_bias(self) -> float | None:
        """The phenotype bias figure shown beside the type.

        Returns:
            The bias, or None if the phenotype block is absent.

        """
        block = self._block("phenotype")
        value = block.get("bias") if block else None
        return value if isinstance(value, int | float) else None

    @property
    def power_extras(self) -> dict | None:
        """Power values with no column: zmap, vo2max, cp, awc.

        Returns:
            A dict of whichever are present, or None if none are.

        """
        block = self._block("power") or {}
        found = {k: block.get(k) for k in ("zmap", "vo2max", "cp", "awc") if block.get(k) is not None}
        return found or None

    @property
    def power_curve_w(self) -> dict[int, float] | None:
        """Best power in watts by duration, from Zwift Racing: 5 seconds up to 20 minutes.

        zauth keys the curve by the duration in seconds, as a string ("1200" is twenty
        minutes); here the keys are ints. **Never render this on a page about other riders**:
        it divided by ``curve_wkg`` at the same duration is the rider's weight, exactly.

        Returns:
            ``{seconds: watts}`` for each duration with a positive number, or None.

        """
        block = self._block("power", "curve_w") or {}
        curve = {
            int(seconds): float(watts)
            for seconds, watts in block.items()
            if str(seconds).isdigit() and isinstance(watts, int | float) and not isinstance(watts, bool) and watts > 0
        }
        return curve or None

    @property
    def peak_ratings(self) -> dict | None:
        """30- and 90-day peak vELO.

        The category that went with each peak is :attr:`peak_categories`, never
        ``category_racing``: that is today's tier, not the one held at a peak ninety days ago.

        Returns:
            A dict with whichever of max30/max90 are present, or None.

        """
        block = self._block("ratings") or {}
        found = {
            key: block.get(f"rating_{key}") for key in ("max30", "max90") if block.get(f"rating_{key}") is not None
        }
        return found or None

    @property
    def peak_categories(self) -> dict | None:
        """The category zwiftracing gave each 30- and 90-day vELO peak (e.g. "Ruby").

        Returns:
            A dict with whichever of max30/max90 are present, or None.

        """
        block = self._block("ratings") or {}
        found = {
            key: block[f"category_{key}"] for key in ("max30", "max90") if isinstance(block.get(f"category_{key}"), str)
        }
        return found or None

    @property
    def velo2(self) -> dict | None:
        """Zwift Racing's vELO 2: the race rating, its 30- and 90-day peaks, their categories, and the six factors.

        A different scale from vELO 1 (a rider might be 650 in vELO 2 and 1,600 in vELO 1),
        so it is never shown or compared as if it were :attr:`velo`. The peaks and categories
        are zwiftracing's own, from its ``velo`` object.

        Returns:
            ``race``, ``max30``, ``max90`` and each factor (``sprint``, ``punch``, ``climb``,
            ``time_trial``, ``endurance``, ``pursuit``), None where missing, with the category
            names ``category``, ``category_max30`` and ``category_max90`` and the women's
            ``category_women``, ``category_women_max30`` and ``category_women_max90`` (sent for
            every rider: :meth:`velo2_women` decides when they apply); or None when the
            profile has no vELO 2 figures.

        """
        block = self._block("velo2")
        if block is None:
            return None
        ratings = _numbers(block, {"race": "race", "max30": "race_max30", "max90": "race_max90"}) or {}
        factors = _numbers(block.get("factors"), _FACTORS) or {}
        found = {"race": None, "max30": None, "max90": None, **dict.fromkeys(_FACTORS), **ratings, **factors}
        if not any(value is not None for value in found.values()):
            return None
        categories = {key: block.get(key) if isinstance(block.get(key), str) else None for key in _VELO2_CATEGORIES}
        return {**found, **{key: value or None for key, value in categories.items()}}

    def velo2_women(self, gender: str | None = None) -> dict | None:
        """Return the women's vELO 2 categories, when they apply to this rider.

        zwiftracing gives every rider a women's category beside the open one, so whether to
        show it is ours to decide: only for a woman, judged by ``gender`` (the member's own
        answer) and then by the profile's own, as :func:`is_woman` reads them.

        Args:
            gender: The member's gender when the rider has an account; blank leaves it to
                the profile.

        Returns:
            ``category``, ``max30`` and ``max90`` -- the women's category of the current
            rating and of each peak, None where missing -- or None when the rider is not a
            woman or has none of them.

        """
        if not is_woman(gender, self.gender):
            return None
        velo2 = self.velo2 or {}
        found = {
            "category": velo2.get("category_women"),
            "max30": velo2.get("category_women_max30"),
            "max90": velo2.get("category_women_max90"),
        }
        return found if any(found.values()) else None

    def velo_women(self, gender: str | None = None) -> dict | None:
        """Return the women's vELO categories, when they apply to this rider.

        vELO 1's counterpart of :meth:`velo2_women`, under the same rule. The current one is
        zauth's ``category.racing_women``, beside ``category.racing`` (``category_racing``);
        the peaks' are ``ratings.category_women_max30``/``90``, beside the mixed ones that
        :attr:`peak_categories` reads.

        Args:
            gender: The member's gender when the rider has an account; blank leaves it to
                the profile.

        Returns:
            ``category``, ``max30`` and ``max90``, None where missing, or None when the rider
            is not a woman or has none of them.

        """
        if not is_woman(gender, self.gender):
            return None
        category = self._block("category") or {}
        ratings = self._block("ratings") or {}
        found = {
            "category": _category_name(category.get("racing_women")),
            "max30": _category_name(ratings.get("category_women_max30")),
            "max90": _category_name(ratings.get("category_women_max90")),
        }
        return found if any(found.values()) else None

    def velo_table(self, gender: str | None = None) -> list[RatingRow]:
        """Lay out vELO's rating and categories, now and at its 30- and 90-day peaks, for the card's table.

        Each peak keeps the category zwiftracing gave it, never today's tier: a peak without
        one is a gap, not ``category_racing``.

        Args:
            gender: The member's gender, which decides the women's row as in :meth:`velo_women`.

        Returns:
            The rows with something in them; empty when the rider has no vELO figures.

        """
        peaks = self.peak_ratings or {}
        mixed = self.peak_categories or {}
        women = self.velo_women(gender) or {}
        return _rating_rows(
            (self.velo, peaks.get("max30"), peaks.get("max90")),
            (self.category_racing or None, mixed.get("max30") or None, mixed.get("max90") or None),
            (women.get("category"), women.get("max30"), women.get("max90")),
        )

    def velo2_table(self, gender: str | None = None) -> list[RatingRow]:
        """Lay out vELO 2's race rating and categories, now and at its peaks, for the card's table.

        Args:
            gender: The member's gender, which decides the women's row as in :meth:`velo2_women`.

        Returns:
            The rows with something in them; empty when the rider has no vELO 2 figures.

        """
        velo2 = self.velo2 or {}
        women = self.velo2_women(gender) or {}
        return _rating_rows(
            (velo2.get("race"), velo2.get("max30"), velo2.get("max90")),
            (velo2.get("category"), velo2.get("category_max30"), velo2.get("category_max90")),
            (women.get("category"), women.get("max30"), women.get("max90")),
        )

    @property
    def seed_ratings(self) -> dict | None:
        """Zwift Racing's seed: the starting estimate behind vELO 2, from the rider's power data.

        Returns:
            ``race``, ``time_trial`` and each factor (``sprint`` … ``pursuit``, with the
            time-trial factor as ``time_trial_factor``), None where missing; or None.

        """
        block = self._block("seed")
        if block is None:
            return None
        ratings = _numbers(block, {"race": "race", "time_trial": "timeTrial"}) or {}
        factor_keys = {("time_trial_factor" if name == "time_trial" else name): key for name, key in _FACTORS.items()}
        factors = _numbers(block.get("factors"), factor_keys) or {}
        found = {"race": None, "time_trial": None, **dict.fromkeys(factor_keys), **ratings, **factors}
        return found if any(value is not None for value in found.values()) else None

    @property
    def race_counts(self) -> dict | None:
        """How many races zwiftracing has for the rider, and how they ended.

        Returns:
            ``finishes``, ``wins``, ``podiums`` and ``dnfs`` as ints, None where missing; or None.

        """
        found = _numbers(self._block("race_counts"), {k: k for k in ("finishes", "wins", "podiums", "dnfs")})
        return {key: None if value is None else int(value) for key, value in found.items()} if found else None

    @property
    def power_curve_wkg(self) -> dict[int, float] | None:
        """Best power in W/kg by duration in seconds, from Zwift Racing: 5 seconds up to 20 minutes.

        Returns:
            ``{seconds: W/kg}`` for each duration with a positive number, or None.

        """
        block = self._block("power", "curve_wkg") or {}
        curve = {
            int(seconds): number
            for seconds, value in block.items()
            if str(seconds).isdigit() and (number := _number(value)) is not None and number > 0
        }
        return curve or None

    @property
    def velo2_factors(self) -> list[tuple[str, float]]:
        """The vELO 2 factors the rider has, named for the card, in zwiftracing's order.

        Returns:
            ``(name, value)`` pairs; empty when there are none.

        """
        velo2 = self.velo2 or {}
        return [(label, velo2[key]) for key, label in _FACTOR_LABELS if velo2.get(key) is not None]

    @property
    def seed_factors(self) -> list[tuple[str, float]]:
        """The seed factors the rider has, named for the card, in zwiftracing's order.

        Returns:
            ``(name, value)`` pairs, the time-trial factor as "TT"; empty when there are none.

        """
        seed = self.seed_ratings or {}
        keys = {key: ("time_trial_factor" if key == "time_trial" else key) for key, _ in _FACTOR_LABELS}
        return [(label, seed[keys[key]]) for key, label in _FACTOR_LABELS if seed.get(keys[key]) is not None]

    @property
    def power_curve(self) -> list[tuple[str, float | None, float | None]]:
        """Best power by duration, as the card's table lays it out.

        Returns:
            ``(duration, W/kg, watts)`` for each of :data:`CURVE_DURATIONS` with either value;
            empty when the rider has no curve.

        """
        wkg = self.power_curve_wkg or {}
        watts = self.power_curve_w or {}
        return [
            (name, wkg.get(seconds), watts.get(seconds))
            for seconds, name in CURVE_DURATIONS
            if seconds in wkg or seconds in watts
        ]


class Velo2Categories(NamedTuple):
    """A rider's vELO 2 categories, as the pages without a loaded profile get them.

    Attributes:
        mixed: The mixed (open) category, which every page shows by default.
        women: A woman's women's category, "" for anyone else -- for filters and columns.

    """

    mixed: str
    women: str


def velo2_categories_by_zwid(riders: Iterable[tuple[int | None, str | None]]) -> dict[int, Velo2Categories]:
    """Look up the vELO 2 categories of some riders, in one query.

    For the pages that do not otherwise load the riders' profiles. Only the two names are
    read out of each payload, not the whole document, so a page listing the team stays one
    light query. The women's category is kept for a woman, by :func:`is_woman` -- the given
    gender first, the profile's when that is blank.

    Args:
        riders: ``(zwid, gender)`` pairs -- the gender each page already shows, else the
            member's own; a blank zwid is skipped.

    Returns:
        ``{zwid: Velo2Categories}`` for each rider with a cached profile.

    """
    genders = {zwid: gender or "" for zwid, gender in riders if zwid}
    if not genders:
        return {}
    rows = RiderProfile.objects.filter(zwid__in=genders).values_list(
        "zwid", "gender", "payload__velo2__category", "payload__velo2__category_women"
    )
    return {
        zwid: Velo2Categories(
            mixed if isinstance(mixed, str) else "",
            women if isinstance(women, str) and is_woman(genders[zwid], profile_gender) else "",
        )
        for zwid, profile_gender, mixed, women in rows
    }
