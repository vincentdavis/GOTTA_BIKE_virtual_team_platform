"""Service layer for unified team roster operations."""

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from django.db.models import Count

from apps.accounts.models import User
from apps.zwiftpower.models import ZPRiderResults, ZPTeamRiders
from apps.zwiftracing.models import ZRRider

# ZwiftPower division to category letter mapping
ZP_DIV_TO_CATEGORY: dict[int, str] = {
    5: "A+",
    10: "A",
    20: "B",
    30: "C",
    40: "D",
    50: "E",
}


@dataclass
class UnifiedRider:
    """Unified rider data from all sources."""

    zwid: int

    # User data
    has_account: bool = False
    user_id: int | None = None
    username: str = ""
    discord_username: str = ""
    zwid_verified: bool = False
    user_gender: str = ""

    # ZwiftPower data
    in_zwiftpower: bool = False
    zp_name: str = ""
    zp_div: int = 0
    zp_divw: int = 0
    zp_date_left: datetime | None = None

    # Zwift Racing data
    in_zwiftracing: bool = False
    zr_name: str = ""
    zr_category: str = ""
    zr_date_left: datetime | None = None

    # ZwiftPower Results (aggregated)
    has_results: bool = False
    result_count: int = 0

    # Race Ready status
    is_race_ready: bool = False

    # Class variable for div mapping
    DIV_TO_CATEGORY: ClassVar[dict[int, str]] = ZP_DIV_TO_CATEGORY

    @property
    def display_name(self) -> str:
        """Return best available name."""
        return self.zp_name or self.zr_name or self.username or f"Rider {self.zwid}"

    @property
    def zp_category(self) -> str:
        """Return ZwiftPower category letter from division number."""
        return ZP_DIV_TO_CATEGORY.get(self.zp_div, "")

    @property
    def zp_category_w(self) -> str:
        """Return ZwiftPower women's category letter from division number."""
        return ZP_DIV_TO_CATEGORY.get(self.zp_divw, "")

    @property
    def gender(self) -> str:
        """Return gender with fallback logic.

        Priority: user profile gender > ZP divw (if > 0 = F, else M).

        Returns:
            'M' for male, 'F' for female, or '' if unknown.

        """
        # First check user profile gender
        if self.user_gender:
            return self.user_gender
        # Fall back to ZwiftPower: if divw > 0, they are female
        if self.in_zwiftpower:
            return "F" if self.zp_divw > 0 else "M"
        return ""

    @property
    def is_active_member(self) -> bool:
        """Check if rider is active in at least one source."""
        return (self.in_zwiftpower and not self.zp_date_left) or (self.in_zwiftracing and not self.zr_date_left)

    @property
    def zp_active(self) -> bool:
        """Check if rider is active in ZwiftPower."""
        return self.in_zwiftpower and not self.zp_date_left

    @property
    def zr_active(self) -> bool:
        """Check if rider is active in Zwift Racing."""
        return self.in_zwiftracing and not self.zr_date_left

    @property
    def membership_status(self) -> str:
        """Return detailed membership status.

        Returns:
            One of: 'both', 'zp_only', 'zr_only', 'left', 'none'

        """
        if self.zp_active and self.zr_active:
            return "both"
        if self.zp_active:
            return "zp_only"
        if self.zr_active:
            return "zr_only"
        if self.in_zwiftpower or self.in_zwiftracing:
            return "left"
        return "none"


def get_unified_team_roster() -> list[UnifiedRider]:
    """Get unified team roster from all data sources.

    Returns:
        List of UnifiedRider objects sorted by display name.

    """
    # Query each source with .values() for efficiency
    users = User.objects.filter(zwid__isnull=False).values(
        "id", "zwid", "username", "discord_username", "zwid_verified", "gender"
    )
    zp_riders = ZPTeamRiders.objects.all().values("zwid", "name", "div", "divw", "date_left")
    zr_riders = ZRRider.objects.all().values("zwid", "name", "race_current_category", "date_left")

    # Get result counts per rider
    result_counts = ZPRiderResults.objects.values("zwid").annotate(count=Count("id"))

    # Get race ready status for users (need full objects for property access)
    user_objects = User.objects.filter(zwid__isnull=False).prefetch_related("race_ready_records")
    race_ready_by_zwid: dict[int, bool] = {u.zwid: u.is_race_ready for u in user_objects}

    # Build lookup dicts
    user_by_zwid: dict[int, dict] = {u["zwid"]: u for u in users}
    zp_by_zwid: dict[int, dict] = {r["zwid"]: r for r in zp_riders}
    zr_by_zwid: dict[int, dict] = {r["zwid"]: r for r in zr_riders}
    results_by_zwid: dict[int, int] = {r["zwid"]: r["count"] for r in result_counts}

    # Collect all unique zwids
    zwid_set: set[int] = set()
    zwid_set.update(user_by_zwid.keys())
    zwid_set.update(zp_by_zwid.keys())
    zwid_set.update(zr_by_zwid.keys())

    # Build unified list
    unified: list[UnifiedRider] = []

    for zwid in zwid_set:
        rider = UnifiedRider(zwid=zwid)

        # User data
        if zwid in user_by_zwid:
            u = user_by_zwid[zwid]
            rider.has_account = True
            rider.user_id = u["id"]
            rider.username = u["username"]
            rider.discord_username = u["discord_username"] or ""
            rider.zwid_verified = u["zwid_verified"]
            rider.user_gender = u["gender"] or ""
            rider.is_race_ready = race_ready_by_zwid.get(zwid, False)

        # ZwiftPower data
        if zwid in zp_by_zwid:
            zp = zp_by_zwid[zwid]
            rider.in_zwiftpower = True
            rider.zp_name = zp["name"]
            rider.zp_div = zp["div"]
            rider.zp_divw = zp["divw"]
            rider.zp_date_left = zp["date_left"]

        # Zwift Racing data
        if zwid in zr_by_zwid:
            zr = zr_by_zwid[zwid]
            rider.in_zwiftracing = True
            rider.zr_name = zr["name"]
            rider.zr_category = zr["race_current_category"] or ""
            rider.zr_date_left = zr["date_left"]

        # Results data
        if zwid in results_by_zwid:
            rider.has_results = True
            rider.result_count = results_by_zwid[zwid]

        unified.append(rider)

    # Sort by display name
    return sorted(unified, key=lambda r: r.display_name.lower())


def get_unified_rider(zwid: int) -> UnifiedRider | None:
    """Get unified data for a single rider.

    Args:
        zwid: The Zwift ID to look up.

    Returns:
        UnifiedRider or None if not found in any source.

    """
    roster = get_unified_team_roster()
    for rider in roster:
        if rider.zwid == zwid:
            return rider
    return None
