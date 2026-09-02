"""Single source of truth for background tasks.

Consumed by:
- ``gotta_bike_platform.management.commands.scheduler`` — registers APScheduler jobs
  for entries with ``scheduled=True``.
- ``apps.accounts.views.config_trigger_task`` and the ``/site/config/background_tasks/``
  UI — renders a Run-Now button per entry and dispatches the trigger.

To add a new task: import the ``@task``-decorated callable here, append an entry to
``TASK_REGISTRY``. If it should run on a schedule, add a matching ``SCHEDULER_*_HOURS``
Constance setting (also list it in the ``Scheduler`` fieldset in ``settings.py``).
Interval changes still require a scheduler restart to take effect.
"""

from typing import Any

from constance import config

from apps.accounts.tasks import (
    clear_expired_sessions,
    guild_member_sync_status,
    refresh_all_race_ready,
    refresh_zwift_racing_metrics,
    sync_guild_members,
    sync_new_member_roles,
    sync_race_ready_roles,
    sync_youtube_channel_ids,
    sync_youtube_videos,
    sync_zr_category_roles,
)
from apps.analytics.tasks import purge_page_visits
from apps.club_strava.tasks import purge_strava_activities, sync_strava_activities
from apps.data_connection.tasks import sync_all_data_connections
from apps.events.tasks import remove_expired_ds_roles
from apps.ladder_planner.tasks import refresh_cached_clubs
from apps.rider_data.tasks import purge_rider_profiles, sync_rider_profiles
from apps.team.tasks import (
    purge_expired_media,
    sync_discord_channels,
    sync_discord_roles,
    warn_expiring_verifications,
)
from apps.user_api.tasks import purge_expired_api_keys
from apps.zwift.tasks import sync_zauth_verifications
from apps.zwift_data.tasks import sync_zwift_data
from apps.zwiftpower.tasks import update_team_results, update_team_riders
from apps.zwiftracing.tasks import sync_zr_riders

# Tasks grouped by what they reach out to, because that is the question being asked when
# something breaks: if zwiftpower.com is down, or Discord is rate-limiting, which jobs fail?
# A task is filed under the service it *talks to*, not the data it happens to be about --
# sync_zr_category_roles reads Zwift Racing data we already hold and writes Discord roles, so
# it is a Discord task: Discord is what has to be up for it to work.
#
# "local" means no external service at all: database work, recomputation, and purges of our
# own storage. Those are the jobs that keep running when everything else is unreachable.
TASK_GROUPS: dict[str, str] = {
    "local": "Local — no external service",
    "discord": "discord.com",
    "zwiftpower": "zwiftpower.com",
    "zwiftracing": "zwiftracing.app",
    "zauth": "zwift.com (via the zauth service)",
    "speedlab": "Zwift Speed Lab",
    "youtube": "youtube.com",
    "strava": "strava.com",
    "google": "Google Sheets",
}

# Local first, then remote services roughly by how much of the app depends on them.
TASK_GROUP_ORDER: tuple[str, ...] = (
    "local",
    "discord",
    "zauth",
    "zwiftpower",
    "zwiftracing",
    "speedlab",
    "youtube",
    "strava",
    "google",
)


def grouped_tasks(tasks: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Group tasks by the service they contact, in display order.

    Args:
        tasks: The registry to group; defaults to ``TASK_REGISTRY``. Passed in by the admin
            view so the grouping applies to entries already enriched with last-run info.

    Returns:
        One dict per non-empty group, each with ``key``, ``label`` and ``tasks``.

    """
    source = TASK_REGISTRY if tasks is None else tasks
    buckets: dict[str, dict[str, dict[str, Any]]] = {key: {} for key in TASK_GROUP_ORDER}
    for task_id, info in source.items():
        # An unrecognised group would otherwise vanish from the page rather than error.
        buckets.setdefault(info.get("group", "local"), {})[task_id] = info

    ordered = list(TASK_GROUP_ORDER) + [k for k in buckets if k not in TASK_GROUP_ORDER]
    return [
        {"key": key, "label": TASK_GROUPS.get(key, key), "tasks": buckets[key]}
        for key in ordered
        if buckets.get(key)
    ]


TASK_REGISTRY: dict[str, dict[str, Any]] = {
    "update_team_riders": {
        "task": update_team_riders,
        "group": "zwiftpower",
        "description": "Fetch team riders from ZwiftPower",
        "scheduled": True,
        "hours_setting": "SCHEDULER_UPDATE_TEAM_RIDERS_HOURS",
    },
    "update_team_results": {
        "task": update_team_results,
        "group": "zwiftpower",
        "description": "Fetch team results from ZwiftPower",
        "scheduled": True,
        "hours_setting": "SCHEDULER_UPDATE_TEAM_RESULTS_HOURS",
    },
    "sync_zr_riders": {
        "task": sync_zr_riders,
        "group": "zwiftracing",
        "description": "Sync riders from Zwift Racing API",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_ZR_RIDERS_HOURS",
    },
    "refresh_zwift_racing_metrics": {
        "task": refresh_zwift_racing_metrics,
        "group": "zauth",
        "description": "Mirror connected riders' zFTP/zMAP from the zauth service",
        "scheduled": True,
        "minutes_setting": "SCHEDULER_REFRESH_ZWIFT_METRICS_MINUTES",
    },
    "sync_guild_members": {
        "task": sync_guild_members,
        "group": "discord",
        "description": "Fetch Discord guild members and sync to database (files ticket on departure)",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_GUILD_MEMBERS_HOURS",
    },
    "guild_member_sync_status": {
        "task": guild_member_sync_status,
        "group": "local",
        "description": "Report guild member sync health metrics",
        "scheduled": True,
        "hours_setting": "SCHEDULER_GUILD_MEMBER_SYNC_STATUS_HOURS",
    },
    "refresh_all_race_ready": {
        "task": refresh_all_race_ready,
        "group": "local",
        "description": "Refresh cached is_race_ready field for all users (handles expiration)",
        "scheduled": True,
        "hours_setting": "SCHEDULER_REFRESH_ALL_RACE_READY_HOURS",
    },
    "sync_race_ready_roles": {
        "task": sync_race_ready_roles,
        "group": "discord",
        "description": "Sync race ready Discord roles for all users based on verification status",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_RACE_READY_ROLES_HOURS",
    },
    "sync_youtube_channel_ids": {
        "task": sync_youtube_channel_ids,
        "group": "local",
        "description": "Extract YouTube channel IDs from user YouTube URLs",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_YOUTUBE_CHANNEL_IDS_HOURS",
    },
    "sync_youtube_videos": {
        "task": sync_youtube_videos,
        "group": "youtube",
        "description": "Fetch new videos from YouTube RSS feeds for all users",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_YOUTUBE_VIDEOS_HOURS",
    },
    "sync_strava_activities": {
        "task": sync_strava_activities,
        "group": "strava",
        "description": "Fetch club activities from Strava",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_STRAVA_ACTIVITIES_HOURS",
    },
    "sync_zr_category_roles": {
        "task": sync_zr_category_roles,
        "group": "discord",
        "description": "Sync ZR category Discord roles based on Zwift Racing data",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_ZR_CATEGORY_ROLES_HOURS",
    },
    "sync_discord_channels": {
        "task": sync_discord_channels,
        "group": "discord",
        "description": "Fetch Discord guild channels and sync to database",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_DISCORD_CHANNELS_HOURS",
    },
    "sync_discord_roles": {
        "task": sync_discord_roles,
        "group": "discord",
        "description": "Fetch Discord guild roles and sync to database",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_DISCORD_ROLES_HOURS",
    },
    "warn_expiring_verifications": {
        "task": warn_expiring_verifications,
        "group": "discord",
        "description": "Send DMs for expiring verifications",
        "scheduled": True,
        "hours_setting": "SCHEDULER_WARN_EXPIRING_VERIFICATIONS_HOURS",
        # days=None makes the task read the EXPIRE_WARNING_DAYS Constance list
        # (e.g. [15, 7, 3, 1]). Do NOT hardcode a single value here — that
        # overrides the setting and only the matching threshold ever fires.
        "kwargs": {"days": None, "dry_run": False},
    },
    "sync_new_member_roles": {
        "task": sync_new_member_roles,
        "group": "discord",
        "description": "Sync New Member Discord role based on guild join date",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_NEW_MEMBER_ROLES_HOURS",
    },
    "sync_data_connections": {
        "task": sync_all_data_connections,
        "group": "google",
        "description": "Sync all data connections with auto_sync enabled to Google Sheets",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_DATA_CONNECTIONS_HOURS",
    },
    "purge_strava_activities": {
        "group": "local",
        "task": purge_strava_activities,
        "description": "Delete Strava club activities older than the retention window",
        "scheduled": True,
        "hours_setting": "SCHEDULER_PURGE_STRAVA_ACTIVITIES_HOURS",
    },
    "purge_page_visits": {
        "task": purge_page_visits,
        "group": "local",
        "description": "Anonymise then delete old page-visit rows",
        "scheduled": True,
        "hours_setting": "SCHEDULER_PURGE_PAGE_VISITS_HOURS",
    },
    "clear_expired_sessions": {
        "task": clear_expired_sessions,
        "group": "local",
        "description": "Delete expired session rows (they carry the user id)",
        "scheduled": True,
        "hours_setting": "SCHEDULER_CLEAR_SESSIONS_HOURS",
    },
    "purge_expired_media": {
        "task": purge_expired_media,
        "group": "local",
        "description": "Delete uploaded media from expired verification records",
        "scheduled": True,
        "hours_setting": "SCHEDULER_PURGE_EXPIRED_MEDIA_HOURS",
    },
    "sync_rider_profiles": {
        "task": sync_rider_profiles,
        "group": "zauth",
        "description": "Refresh cached rider profiles from the zauth unified source",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_RIDER_PROFILES_HOURS",
    },
    # Deliberately manual for now. The model landed before any consumer, so nothing has yet
    # validated that last_race_at is populated correctly -- scheduling a delete against an
    # anchor nobody has checked is how you lose data quietly. Register it as schedulable once
    # the sync has run for a while and the values look right.
    "purge_rider_profiles": {
        "task": purge_rider_profiles,
        "group": "local",
        "description": "Delete cached rider profiles whose last known race is outside the retention window",
    },
    "purge_expired_api_keys": {
        "task": purge_expired_api_keys,
        "group": "local",
        "description": "Hard-delete user API keys that expired more than 90 days ago",
        "scheduled": True,
        "hours_setting": "SCHEDULER_PURGE_EXPIRED_API_KEYS_HOURS",
    },
    "remove_expired_ds_roles": {
        "task": remove_expired_ds_roles,
        "group": "discord",
        "description": "Remove squad roles from Directeurs Sportifs after their race has finished",
        "scheduled": True,
        "hours_setting": "SCHEDULER_REMOVE_EXPIRED_DS_ROLES_HOURS",
    },
    "refresh_cached_clubs": {
        "task": refresh_cached_clubs,
        "group": "zwiftracing",
        "description": "Refresh cached opponent clubs used by recent ladder matchups",
        "scheduled": True,
        "hours_setting": "SCHEDULER_REFRESH_CACHED_CLUBS_HOURS",
    },
    "sync_zwift_data": {
        "task": sync_zwift_data,
        "group": "speedlab",
        "description": "Re-sync the Zwift Speed Lab route/segment/world dataset",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_ZWIFT_DATA_HOURS",
    },
    "sync_zauth_verifications": {
        "task": sync_zauth_verifications,
        "group": "zauth",
        "description": "Reconcile Zwift OAuth (zauth) verification status from the zwift service",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_ZAUTH_VERIFICATIONS_HOURS",
    },
}


def resolve_interval_minutes(info: dict[str, Any]) -> float:
    """Resolve one registry entry's interval to minutes.

    Most tasks are configured in whole hours, which is the right granularity for a
    ZwiftPower scrape or a nightly sweep. A task that mirrors webhook-driven data
    wants finer control than that, so an entry may declare ``minutes_setting``
    instead -- exactly one of the two.

    Args:
        info: A ``TASK_REGISTRY`` entry.

    Returns:
        The interval in minutes.

    """
    if "minutes_setting" in info:
        return float(getattr(config, info["minutes_setting"]))
    return float(getattr(config, info["hours_setting"])) * 60


def get_scheduled_tasks() -> list[dict[str, Any]]:
    """Return scheduled entries with their resolved interval in minutes.

    Reads the Constance setting for each scheduled task at call time, so a scheduler
    restart picks up edits made in the admin UI. Minutes is the canonical unit here
    because it is the finer of the two an entry may be configured in.

    Returns:
        List of dicts with ``id``, ``task``, ``description``, ``minutes``, and
        ``kwargs`` keys, one per task with ``scheduled=True``.

    """
    jobs: list[dict[str, Any]] = []
    for task_id, info in TASK_REGISTRY.items():
        if not info.get("scheduled"):
            continue
        jobs.append({
            "id": task_id,
            "task": info["task"],
            "description": info["description"],
            "minutes": resolve_interval_minutes(info),
            "kwargs": info.get("kwargs", {}),
        })
    return jobs
