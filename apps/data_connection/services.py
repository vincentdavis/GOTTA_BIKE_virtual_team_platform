"""Services for data_connection app."""

from typing import Any

import logfire
from django.utils import timezone

from apps.accounts.models import User
from apps.data_connection import gs_client
from apps.data_connection.models import DataConnection
from apps.zwiftpower.models import ZPTeamRiders
from apps.zwiftracing.models import ZRRider


def _get_field_value(
    field_key: str,
    user: dict | None,
    zp: dict | None,
    zr: dict | None,
    race_ready_by_zwid: dict[int, bool] | None = None,
) -> str:
    """Get value for a field key from the appropriate source.

    Args:
        field_key: The field key to look up.
        user: User data dict (or None).
        zp: ZwiftPower data dict (or None).
        zr: Zwift Racing data dict (or None).
        race_ready_by_zwid: Mapping of zwid to race_ready status (for computed property).

    Returns:
        String value for the field, or empty string if not found.

    """
    # Base fields
    if field_key == "zwid":
        # Priority: user > zp > zr
        if user:
            return str(user.get("zwid", ""))
        if zp:
            return str(zp.get("zwid", ""))
        if zr:
            return str(zr.get("zwid", ""))
        return ""
    if field_key == "discord_username":
        return str(user.get("discord_username", "") or "") if user else ""
    if field_key == "discord_id":
        return str(user.get("discord_id", "") or "") if user else ""

    # User fields
    if field_key == "first_name":
        return str(user.get("first_name", "") or "") if user else ""
    if field_key == "last_name":
        return str(user.get("last_name", "") or "") if user else ""
    if field_key == "birth_year":
        val = user.get("birth_year") if user else None
        return str(val) if val else ""
    if field_key == "city":
        return str(user.get("city", "") or "") if user else ""
    if field_key == "country":
        return str(user.get("country", "") or "") if user else ""
    if field_key == "gender":
        return str(user.get("gender", "") or "") if user else ""
    if field_key == "youtube_channel":
        return str(user.get("youtube_channel", "") or "") if user else ""
    if field_key == "race_ready":
        if user and race_ready_by_zwid:
            zwid = user.get("zwid")
            if zwid and zwid in race_ready_by_zwid:
                return "Yes" if race_ready_by_zwid[zwid] else "No"
        return ""

    # ZwiftPower fields (zp_ prefix)
    if field_key.startswith("zp_") and zp:
        zp_key = field_key[3:]  # Remove 'zp_' prefix
        val = zp.get(zp_key)
        return str(val) if val is not None else ""

    # Zwift Racing fields (zr_ prefix)
    if field_key.startswith("zr_") and zr:
        zr_key = field_key[3:]  # Remove 'zr_' prefix
        val = zr.get(zr_key)
        return str(val) if val is not None else ""

    return ""


def _passes_filters(
    filters: dict,
    user: dict | None,
    zp: dict | None,
    zr: dict | None,
) -> bool:
    """Check if a rider passes all configured filters.

    Args:
        filters: Dictionary of filter criteria.
        user: User data dict (or None).
        zp: ZwiftPower data dict (or None).
        zr: Zwift Racing data dict (or None).

    Returns:
        True if rider passes all filters, False otherwise.

    """
    if not filters:
        return True

    # Gender filter (check user gender or ZR gender)
    if "gender" in filters:
        gender = filters["gender"]
        user_gender = user.get("gender") if user else None
        zr_gender = zr.get("gender") if zr else None
        if user_gender != gender and zr_gender != gender:
            return False

    # ZP Division filter
    if "zp_div" in filters:
        if not zp:
            return False
        zp_div = zp.get("div")
        if zp_div != int(filters["zp_div"]):
            return False

    # ZP Women's Division filter
    if "zp_divw" in filters:
        if not zp:
            return False
        zp_divw = zp.get("divw")
        if zp_divw != int(filters["zp_divw"]):
            return False

    # ZP Skill Race minimum filter
    if "zp_skill_race_min" in filters:
        if not zp:
            return False
        skill_race = zp.get("skill_race") or 0
        if skill_race < filters["zp_skill_race_min"]:
            return False

    # ZR Current Rating range filter
    if "zr_rating_min" in filters:
        if not zr:
            return False
        rating = zr.get("race_current_rating")
        if rating is None or float(rating) < filters["zr_rating_min"]:
            return False

    if "zr_rating_max" in filters:
        if not zr:
            return False
        rating = zr.get("race_current_rating")
        if rating is None or float(rating) > filters["zr_rating_max"]:
            return False

    # ZR Phenotype filter
    if "zr_phenotype" in filters:
        if not zr:
            return False
        phenotype = zr.get("phenotype_value") or ""
        if phenotype != filters["zr_phenotype"]:
            return False

    return True


def sync_connection(connection: DataConnection) -> int:
    """Sync a data connection to Google Sheets.

    Clears the sheet and writes all data with headers.

    Args:
        connection: The DataConnection to sync.

    Returns:
        Number of rows written.

    """
    with logfire.span("sync_data_connection", connection_id=connection.id, title=connection.title):
        # Build list of all fields to export
        all_fields = list(DataConnection.BASE_FIELDS) + connection.selected_fields

        # Build display name mapping for headers
        field_display_map: dict[str, str] = {
            "zwid": "Zwift ID",
            "discord_username": "Discord Username",
            "discord_id": "Discord ID",
        }
        field_display_map.update(dict(DataConnection.USER_FIELDS))
        field_display_map.update(dict(DataConnection.ZWIFTPOWER_FIELDS))
        field_display_map.update(dict(DataConnection.ZWIFTRACING_FIELDS))

        headers = [field_display_map.get(f, f) for f in all_fields]

        # Fetch all data sources
        users = User.objects.filter(zwid__isnull=False).values(
            "zwid",
            "discord_username",
            "discord_id",
            "first_name",
            "last_name",
            "birth_year",
            "city",
            "country",
            "gender",
            "youtube_channel",
        )

        # Get race ready status (computed property requires full objects)
        race_ready_by_zwid: dict[int, bool] = {}
        if "race_ready" in all_fields:
            user_objects = User.objects.filter(zwid__isnull=False).prefetch_related("race_ready_records")
            race_ready_by_zwid = {u.zwid: u.is_race_ready for u in user_objects}
        zp_riders = ZPTeamRiders.objects.all().values(
            "zwid",
            "aid",
            "name",
            "flag",
            "age",
            "div",
            "divw",
            "r",
            "rank",
            "ftp",
            "weight",
            "skill",
            "skill_race",
            "skill_seg",
            "skill_power",
            "distance",
            "climbed",
            "energy",
            "time",
            "h_1200_watts",
            "h_1200_wkg",
            "h_15_watts",
            "h_15_wkg",
            "status",
            "reg",
            "zada",
            "date_left",
        )
        zr_riders = ZRRider.objects.all().values(
            "zwid",
            "name",
            "gender",
            "country",
            "age",
            "height",
            "weight",
            "zp_category",
            "zp_ftp",
            "power_wkg5",
            "power_wkg15",
            "power_wkg30",
            "power_wkg60",
            "power_wkg120",
            "power_wkg300",
            "power_wkg1200",
            "power_w5",
            "power_w15",
            "power_w30",
            "power_w60",
            "power_w120",
            "power_w300",
            "power_w1200",
            "power_cp",
            "power_awc",
            "power_compound_score",
            "race_current_rating",
            "race_current_category",
            "race_max30_rating",
            "race_max30_category",
            "race_max90_rating",
            "race_max90_category",
            "race_finishes",
            "race_dnfs",
            "race_wins",
            "race_podiums",
            "handicap_flat",
            "handicap_rolling",
            "handicap_hilly",
            "handicap_mountainous",
            "phenotype_value",
            "phenotype_sprinter",
            "phenotype_puncheur",
            "phenotype_pursuiter",
            "phenotype_climber",
            "phenotype_tt",
            "club_name",
            "date_left",
            "seed_race",
            "seed_time_trial",
            "seed_endurance",
            "seed_pursuit",
            "seed_sprint",
            "seed_punch",
            "seed_climb",
            "seed_tt_factor",
            "velo_race",
            "velo_time_trial",
            "velo_endurance",
            "velo_pursuit",
            "velo_sprint",
            "velo_punch",
            "velo_climb",
            "velo_tt_factor",
        )

        # Build lookup dicts by zwid
        user_by_zwid: dict[int, dict[str, Any]] = {u["zwid"]: u for u in users}
        zp_by_zwid: dict[int, dict[str, Any]] = {r["zwid"]: r for r in zp_riders}
        zr_by_zwid: dict[int, dict[str, Any]] = {r["zwid"]: r for r in zr_riders}

        # Collect all unique zwids
        zwid_set: set[int] = set()
        zwid_set.update(user_by_zwid.keys())
        zwid_set.update(zp_by_zwid.keys())
        zwid_set.update(zr_by_zwid.keys())

        # Build rows (applying filters)
        filters = connection.filters or {}
        rows: list[list[str]] = []
        for zwid in sorted(zwid_set):
            user = user_by_zwid.get(zwid)
            zp = zp_by_zwid.get(zwid)
            zr = zr_by_zwid.get(zwid)

            # Skip riders that don't pass filters
            if not _passes_filters(filters, user, zp, zr):
                continue

            row = [_get_field_value(field, user, zp, zr, race_ready_by_zwid) for field in all_fields]
            rows.append(row)

        # Write to Google Sheets
        row_count = gs_client.clear_and_write_data(
            spreadsheet_url=connection.spreadsheet_url,
            sheet_name=connection.data_sheet,
            headers=headers,
            rows=rows,
        )

        # Update last synced timestamp
        connection.date_last_synced = timezone.now()
        connection.save(update_fields=["date_last_synced"])

        logfire.info(
            f"Synced {row_count} rows to {connection.title}",
            connection_id=connection.id,
            field_count=len(all_fields),
        )

        return row_count
