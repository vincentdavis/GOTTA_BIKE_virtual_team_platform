"""Service layer for unified team roster operations."""

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import ClassVar

from constance import config
from django.db.models import Count, OuterRef, Subquery

from apps.accounts.models import User
from apps.team.models import RaceReadyRecord
from apps.zwiftpower.models import ZPRiderResults, ZPTeamRiders
from apps.zwiftracing.models import ZRRider

# Default verification types when no ZwiftPower category is found
DEFAULT_VERIFICATION_TYPES: list[str] = ["weight_light", "height"]

# ZwiftPower division to category letter mapping
ZP_DIV_TO_CATEGORY: dict[int, str] = {
    5: "A+",
    10: "A",
    20: "B",
    30: "C",
    40: "D",
    50: "E",
}


def get_user_verification_types(user: User) -> list[str]:
    """Get allowed verification types based on user's ZwiftPower category.

    Looks up the user's ZwiftPower division (div for male, divw for female)
    and returns the verification types required for that category from
    the CATEGORY_REQUIREMENTS Constance setting.

    Args:
        user: The user to get verification types for.

    Returns:
        List of verify_type values the user can submit.
        Defaults to ["weight_light", "height"] if no category found.

    """
    if not user.zwid:
        return DEFAULT_VERIFICATION_TYPES

    # Look up ZwiftPower rider
    zp_rider = ZPTeamRiders.objects.filter(zwid=user.zwid).first()
    if not zp_rider:
        return DEFAULT_VERIFICATION_TYPES

    # Get category based on gender: female uses divw, everyone else uses div
    category = zp_rider.divw if user.gender == "female" else zp_rider.div

    if not category:
        return DEFAULT_VERIFICATION_TYPES

    # Look up requirements from Constance
    try:
        requirements = json.loads(config.CATEGORY_REQUIREMENTS)
        types = requirements.get(str(category), DEFAULT_VERIFICATION_TYPES)
        return types if types else DEFAULT_VERIFICATION_TYPES
    except (json.JSONDecodeError, TypeError):
        return DEFAULT_VERIFICATION_TYPES


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
    zp_rank: Decimal | None = None
    zp_ftp: int | None = None
    zp_weight: Decimal | None = None

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
        # First check user profile gender (normalize from 'male'/'female' to 'M'/'F')
        if self.user_gender:
            if self.user_gender == "male":
                return "M"
            if self.user_gender == "female":
                return "F"
            return ""  # 'other' or unknown
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

    @property
    def wkg(self) -> Decimal | None:
        """Calculate watts per kilogram from FTP and weight.

        Returns:
            FTP divided by weight, rounded to 2 decimal places, or None if either is missing.

        """
        if self.zp_ftp is not None and self.zp_weight is not None and self.zp_weight > 0:
            return round(Decimal(self.zp_ftp) / self.zp_weight, 2)
        return None


def get_unified_team_roster() -> list[UnifiedRider]:
    """Get unified team roster from all data sources.

    Returns:
        List of UnifiedRider objects sorted by display name.

    """
    # Query each source with .values() for efficiency
    users = User.objects.filter(zwid__isnull=False).values(
        "id", "zwid", "username", "discord_username", "zwid_verified", "gender"
    )
    zp_riders = ZPTeamRiders.objects.all().values(
        "zwid", "name", "div", "divw", "date_left", "rank", "ftp", "weight"
    )
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
            rider.zp_rank = zp["rank"]
            rider.zp_ftp = zp["ftp"]
            rider.zp_weight = zp["weight"]

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


@dataclass
class PerformanceRider:
    """Performance review data combining verification records with ZwiftPower results."""

    zwid: int
    display_name: str = ""
    zp_div: int = 0
    gender: str = ""
    has_account: bool = False

    # Verification data - weight light
    weight_light_date: datetime | None = None
    weight_light_value: Decimal | None = None

    # Verification data - weight full
    weight_full_date: datetime | None = None
    weight_full_value: Decimal | None = None

    # Verification data - height
    height_date: datetime | None = None
    height_value: int | None = None

    # ZwiftPower result data
    zp_result_date: datetime | None = None
    zp_result_weight: Decimal | None = None
    zp_height_date: datetime | None = None
    zp_height_value: int | None = None

    # FTP data (from ZPTeamRiders and history)
    ftp_current: int | None = None
    ftp_min: int | None = None
    ftp_max: int | None = None

    # ZwiftPower current weight (for WKG calculation)
    zp_weight: Decimal | None = None

    # Class variable for div mapping
    DIV_TO_CATEGORY: ClassVar[dict[int, str]] = ZP_DIV_TO_CATEGORY

    @property
    def zp_category(self) -> str:
        """Return ZwiftPower category letter from division number."""
        return ZP_DIV_TO_CATEGORY.get(self.zp_div, "")

    @property
    def latest_verification_weight(self) -> Decimal | None:
        """Return the most recent verification weight (full or light)."""
        if self.weight_full_date and self.weight_light_date:
            if self.weight_full_date >= self.weight_light_date:
                return self.weight_full_value
            return self.weight_light_value
        if self.weight_full_value is not None:
            return self.weight_full_value
        return self.weight_light_value

    @property
    def weight_diff(self) -> Decimal | None:
        """Difference between latest verification weight and ZP result weight.

        Returns:
            Positive if ZP weight is higher than verification, negative if lower.
            None if either value is missing.

        """
        verification_weight = self.latest_verification_weight
        if verification_weight is None or self.zp_result_weight is None:
            return None
        return self.zp_result_weight - verification_weight

    @property
    def weight_diff_abs(self) -> Decimal | None:
        """Absolute value of weight difference for sorting."""
        diff = self.weight_diff
        return abs(diff) if diff is not None else None

    @property
    def has_weight_concern(self) -> bool:
        """True if weight diff > 2kg."""
        diff = self.weight_diff_abs
        return diff is not None and diff > 2

    @property
    def has_severe_weight_concern(self) -> bool:
        """True if weight diff > 5kg."""
        diff = self.weight_diff_abs
        return diff is not None and diff > 5

    @property
    def wkg(self) -> Decimal | None:
        """Calculate watts per kilogram from FTP and weight.

        Returns:
            FTP divided by weight, rounded to 2 decimal places, or None if either is missing.

        """
        if self.ftp_current is not None and self.zp_weight is not None and self.zp_weight > 0:
            return round(Decimal(self.ftp_current) / self.zp_weight, 2)
        return None


def get_performance_review_data() -> list[PerformanceRider]:
    """Get performance review data for all riders (outer join of ZP, ZR, Users).

    Includes all riders from ZwiftPower, Zwift Racing, and Users,
    excluding those who have left (zp_date_left is set).

    Returns:
        List of PerformanceRider objects sorted by display name.

    """
    # Get unified roster for basic rider info (outer join of all sources)
    roster = get_unified_team_roster()

    # Filter out riders who have left ZwiftPower
    roster = [r for r in roster if not r.zp_date_left]

    # Build lookup of rider info by zwid
    rider_info: dict[int, dict] = {}
    for r in roster:
        rider_info[r.zwid] = {
            "display_name": r.display_name,
            "zp_div": r.zp_div,
            "gender": r.gender,
            "has_account": r.has_account,
        }

    if not rider_info:
        return []

    # Get most recent verified records per user per type using subquery
    # First, get the latest verified record ID for each user/type combination
    latest_verified_subquery = RaceReadyRecord.objects.filter(
        user__zwid=OuterRef("user__zwid"),
        verify_type=OuterRef("verify_type"),
        status=RaceReadyRecord.Status.VERIFIED,
    ).order_by("-reviewed_date").values("pk")[:1]

    verified_records = RaceReadyRecord.objects.filter(
        status=RaceReadyRecord.Status.VERIFIED,
        user__zwid__in=rider_info.keys(),
        pk__in=Subquery(latest_verified_subquery),
    ).select_related("user").values(
        "user__zwid", "verify_type", "weight", "height", "reviewed_date"
    )

    # Build lookup of verification data by zwid
    verification_by_zwid: dict[int, dict] = {}
    for record in verified_records:
        zwid = record["user__zwid"]
        if zwid not in verification_by_zwid:
            verification_by_zwid[zwid] = {}

        verify_type = record["verify_type"]
        if verify_type == "weight_light":
            verification_by_zwid[zwid]["weight_light_date"] = record["reviewed_date"]
            verification_by_zwid[zwid]["weight_light_value"] = record["weight"]
        elif verify_type == "weight_full":
            verification_by_zwid[zwid]["weight_full_date"] = record["reviewed_date"]
            verification_by_zwid[zwid]["weight_full_value"] = record["weight"]
        elif verify_type == "height":
            verification_by_zwid[zwid]["height_date"] = record["reviewed_date"]
            verification_by_zwid[zwid]["height_value"] = record["height"]

    # Get most recent ZP result with weight/height for each rider
    zp_data_by_zwid: dict[int, dict] = {}
    for zwid in rider_info:
        history = ZPRiderResults.get_weight_height_history(zwid)
        if history:
            # Find most recent with weight
            for event_date, weight, height in history:
                if weight is not None and "zp_result_date" not in zp_data_by_zwid.get(zwid, {}):
                    if zwid not in zp_data_by_zwid:
                        zp_data_by_zwid[zwid] = {}
                    zp_data_by_zwid[zwid]["zp_result_date"] = event_date
                    zp_data_by_zwid[zwid]["zp_result_weight"] = weight
                if height is not None and "zp_height_date" not in zp_data_by_zwid.get(zwid, {}):
                    if zwid not in zp_data_by_zwid:
                        zp_data_by_zwid[zwid] = {}
                    zp_data_by_zwid[zwid]["zp_height_date"] = event_date
                    zp_data_by_zwid[zwid]["zp_height_value"] = height
                # Stop if we have both
                if zwid in zp_data_by_zwid:
                    zp_info = zp_data_by_zwid[zwid]
                    if "zp_result_date" in zp_info and "zp_height_date" in zp_info:
                        break

    # Get current FTP and weight from ZPTeamRiders
    zp_riders = ZPTeamRiders.objects.filter(zwid__in=rider_info.keys()).values("zwid", "ftp", "weight")
    ftp_current_by_zwid: dict[int, int | None] = {r["zwid"]: r["ftp"] for r in zp_riders}
    weight_by_zwid: dict[int, Decimal | None] = {r["zwid"]: r["weight"] for r in zp_riders}

    # Get FTP history (min/max) for each rider
    ftp_stats_by_zwid: dict[int, dict] = {}
    for zwid in rider_info:
        ftp_history = ZPTeamRiders.get_ftp_history(zwid)
        if ftp_history:
            ftp_values = [ftp for _, ftp in ftp_history if ftp is not None]
            if ftp_values:
                ftp_stats_by_zwid[zwid] = {
                    "ftp_min": min(ftp_values),
                    "ftp_max": max(ftp_values),
                }

    # Build PerformanceRider objects
    performance_riders: list[PerformanceRider] = []
    for zwid, info in rider_info.items():
        rider = PerformanceRider(
            zwid=zwid,
            display_name=info["display_name"],
            zp_div=info["zp_div"],
            gender=info["gender"],
            has_account=info["has_account"],
        )

        # Add verification data
        if zwid in verification_by_zwid:
            v = verification_by_zwid[zwid]
            rider.weight_light_date = v.get("weight_light_date")
            rider.weight_light_value = v.get("weight_light_value")
            rider.weight_full_date = v.get("weight_full_date")
            rider.weight_full_value = v.get("weight_full_value")
            rider.height_date = v.get("height_date")
            rider.height_value = v.get("height_value")

        # Add ZP data
        if zwid in zp_data_by_zwid:
            zp = zp_data_by_zwid[zwid]
            rider.zp_result_date = zp.get("zp_result_date")
            rider.zp_result_weight = zp.get("zp_result_weight")
            rider.zp_height_date = zp.get("zp_height_date")
            rider.zp_height_value = zp.get("zp_height_value")

        # Add FTP and weight data
        rider.ftp_current = ftp_current_by_zwid.get(zwid)
        rider.zp_weight = weight_by_zwid.get(zwid)
        if zwid in ftp_stats_by_zwid:
            ftp_stats = ftp_stats_by_zwid[zwid]
            rider.ftp_min = ftp_stats.get("ftp_min")
            rider.ftp_max = ftp_stats.get("ftp_max")

        performance_riders.append(rider)

    # Sort by display name
    return sorted(performance_riders, key=lambda r: r.display_name.lower())


@dataclass
class MembershipReviewRider:
    """Member data for membership review view."""

    zwid: int
    user_id: int | None = None

    # User data
    full_name: str = ""
    discord_id: str = ""
    discord_nickname: str = ""

    # ZP/ZR names
    zp_name: str = ""
    zr_name: str = ""

    # Status
    gender: str = ""
    has_account: bool = True
    zwid_verified: bool = False

    # Category
    zp_div: int = 0

    # ZP/ZR membership status
    in_zwiftpower: bool = False
    in_zwiftracing: bool = False
    zp_date_left: datetime | None = None
    zr_date_left: datetime | None = None

    # Results data
    result_count: int = 0
    last_result_date: datetime | None = None

    # Class variable for div mapping
    DIV_TO_CATEGORY: ClassVar[dict[int, str]] = ZP_DIV_TO_CATEGORY

    @property
    def zp_category(self) -> str:
        """Return ZwiftPower category letter from division number."""
        return ZP_DIV_TO_CATEGORY.get(self.zp_div, "")

    @property
    def days_since_result(self) -> int | None:
        """Return days since last result, or None if no results."""
        if not self.last_result_date:
            return None
        from django.utils import timezone
        delta = timezone.now() - self.last_result_date
        return delta.days

    @property
    def discord_profile_url(self) -> str:
        """Return Discord profile URL for this user."""
        if self.discord_id:
            return f"https://discord.com/users/{self.discord_id}"
        return ""

    @property
    def zp_active(self) -> bool:
        """Check if rider is active in ZwiftPower."""
        return self.in_zwiftpower and not self.zp_date_left

    @property
    def zr_active(self) -> bool:
        """Check if rider is active in Zwift Racing."""
        return self.in_zwiftracing and not self.zr_date_left

    @property
    def is_active_member(self) -> bool:
        """Check if rider is active in at least one source."""
        return self.zp_active or self.zr_active

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


def get_membership_review_data() -> list[MembershipReviewRider]:
    """Get membership review data with outer join across all sources.

    Performs an outer join across Users, ZwiftPower, and Zwift Racing to show
    all riders from any source, not just those with user accounts.

    Returns:
        List of MembershipReviewRider objects sorted by display name.

    """
    from django.db.models import Max

    # Query each source independently
    users = User.objects.filter(zwid__isnull=False).values(
        "id", "zwid", "first_name", "last_name", "discord_id", "discord_nickname", "discord_username",
        "gender", "zwid_verified"
    )
    zp_riders = ZPTeamRiders.objects.all().values("zwid", "name", "div", "divw", "date_left")
    zr_riders = ZRRider.objects.all().values("zwid", "name", "date_left")

    # Build lookup dicts
    user_by_zwid: dict[int, dict] = {u["zwid"]: u for u in users}
    zp_by_zwid: dict[int, dict] = {r["zwid"]: r for r in zp_riders}
    zr_by_zwid: dict[int, dict] = {r["zwid"]: r for r in zr_riders}

    # Get result counts and last result date per rider
    result_stats = (
        ZPRiderResults.objects
        .values("zwid")
        .annotate(
            count=Count("id"),
            last_result=Max("event__event_date")
        )
    )
    results_by_zwid: dict[int, dict] = {
        r["zwid"]: {"count": r["count"], "last_result": r["last_result"]}
        for r in result_stats
    }

    # Collect ALL unique zwids (outer join)
    zwid_set: set[int] = set()
    zwid_set.update(user_by_zwid.keys())
    zwid_set.update(zp_by_zwid.keys())
    zwid_set.update(zr_by_zwid.keys())

    # Build MembershipReviewRider objects for each unique zwid
    riders: list[MembershipReviewRider] = []
    for zwid in zwid_set:
        rider = MembershipReviewRider(zwid=zwid, has_account=False)

        # Add user data if exists
        if zwid in user_by_zwid:
            u = user_by_zwid[zwid]
            rider.user_id = u["id"]
            rider.has_account = True
            rider.zwid_verified = u["zwid_verified"]
            rider.discord_id = u["discord_id"] or ""
            rider.discord_nickname = u["discord_nickname"] or u["discord_username"] or ""

            # Build full name
            first = u["first_name"] or ""
            last = u["last_name"] or ""
            rider.full_name = f"{first} {last}".strip()

            # Normalize gender
            if u["gender"] == "male":
                rider.gender = "M"
            elif u["gender"] == "female":
                rider.gender = "F"

        # Add ZP data
        if zwid in zp_by_zwid:
            zp = zp_by_zwid[zwid]
            rider.in_zwiftpower = True
            rider.zp_name = zp["name"]
            rider.zp_div = zp["div"]
            rider.zp_date_left = zp["date_left"]

            # Fall back to ZP gender if user gender not set
            if not rider.gender:
                rider.gender = "F" if zp["divw"] and zp["divw"] > 0 else "M"

        # Add ZR data
        if zwid in zr_by_zwid:
            zr = zr_by_zwid[zwid]
            rider.in_zwiftracing = True
            rider.zr_name = zr["name"]
            rider.zr_date_left = zr["date_left"]

        # Add results data
        if zwid in results_by_zwid:
            stats = results_by_zwid[zwid]
            rider.result_count = stats["count"]
            rider.last_result_date = stats["last_result"]

        riders.append(rider)

    # Sort by best available name
    return sorted(riders, key=lambda r: (r.full_name or r.zp_name or r.zr_name or "").lower())
