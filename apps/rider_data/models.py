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

from django.db import models

from gotta_bike_platform.retention import RetentionPolicy


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
        return any((self.power_extras, self.peak_ratings, self.handicaps, self.totals))

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
    def peak_ratings(self) -> dict | None:
        """30- and 90-day peak vELO.

        The CATEGORY that went with each peak is deliberately absent: zauth carries one racing
        category, the current one, so these are bare numbers. Labelling them with
        ``category_racing`` would attach today's tier to a rating from ninety days ago.

        Returns:
            A dict with whichever of max30/max90 are present, or None.

        """
        block = self._block("ratings") or {}
        found = {
            key: block.get(f"rating_{key}") for key in ("max30", "max90") if block.get(f"rating_{key}") is not None
        }
        return found or None
