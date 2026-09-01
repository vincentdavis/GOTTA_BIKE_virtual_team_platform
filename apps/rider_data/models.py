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
    fetched_at = models.DateTimeField(db_index=True, help_text="When we last called zauth for this rider")
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
        "Anchored on last known race rather than fetch time, so an active teammate's row is not "
        "aged out merely because nothing has looked them up lately.",
        anchor="last_race_at",
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
