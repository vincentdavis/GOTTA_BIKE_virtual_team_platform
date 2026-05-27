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
    guild_member_sync_status,
    refresh_all_race_ready,
    sync_guild_members,
    sync_new_member_roles,
    sync_race_ready_roles,
    sync_youtube_channel_ids,
    sync_youtube_videos,
    sync_zr_category_roles,
)
from apps.club_strava.tasks import sync_strava_activities
from apps.data_connection.tasks import sync_all_data_connections
from apps.team.tasks import sync_discord_channels, sync_discord_roles, warn_expiring_verifications
from apps.user_api.tasks import purge_expired_api_keys
from apps.zwiftpower.tasks import update_team_results, update_team_riders
from apps.zwiftracing.tasks import sync_zr_riders

TASK_REGISTRY: dict[str, dict[str, Any]] = {
    "update_team_riders": {
        "task": update_team_riders,
        "description": "Fetch team riders from ZwiftPower",
        "scheduled": True,
        "hours_setting": "SCHEDULER_UPDATE_TEAM_RIDERS_HOURS",
    },
    "update_team_results": {
        "task": update_team_results,
        "description": "Fetch team results from ZwiftPower",
        "scheduled": True,
        "hours_setting": "SCHEDULER_UPDATE_TEAM_RESULTS_HOURS",
    },
    "sync_zr_riders": {
        "task": sync_zr_riders,
        "description": "Sync riders from Zwift Racing API",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_ZR_RIDERS_HOURS",
    },
    "sync_guild_members": {
        "task": sync_guild_members,
        "description": "Fetch Discord guild members and sync to database (files ticket on departure)",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_GUILD_MEMBERS_HOURS",
    },
    "guild_member_sync_status": {
        "task": guild_member_sync_status,
        "description": "Report guild member sync health metrics",
        "scheduled": True,
        "hours_setting": "SCHEDULER_GUILD_MEMBER_SYNC_STATUS_HOURS",
    },
    "refresh_all_race_ready": {
        "task": refresh_all_race_ready,
        "description": "Refresh cached is_race_ready field for all users (handles expiration)",
        "scheduled": True,
        "hours_setting": "SCHEDULER_REFRESH_ALL_RACE_READY_HOURS",
    },
    "sync_race_ready_roles": {
        "task": sync_race_ready_roles,
        "description": "Sync race ready Discord roles for all users based on verification status",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_RACE_READY_ROLES_HOURS",
    },
    "sync_youtube_channel_ids": {
        "task": sync_youtube_channel_ids,
        "description": "Extract YouTube channel IDs from user YouTube URLs",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_YOUTUBE_CHANNEL_IDS_HOURS",
    },
    "sync_youtube_videos": {
        "task": sync_youtube_videos,
        "description": "Fetch new videos from YouTube RSS feeds for all users",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_YOUTUBE_VIDEOS_HOURS",
    },
    "sync_strava_activities": {
        "task": sync_strava_activities,
        "description": "Fetch club activities from Strava",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_STRAVA_ACTIVITIES_HOURS",
    },
    "sync_zr_category_roles": {
        "task": sync_zr_category_roles,
        "description": "Sync ZR category Discord roles based on Zwift Racing data",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_ZR_CATEGORY_ROLES_HOURS",
    },
    "sync_discord_channels": {
        "task": sync_discord_channels,
        "description": "Fetch Discord guild channels and sync to database",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_DISCORD_CHANNELS_HOURS",
    },
    "sync_discord_roles": {
        "task": sync_discord_roles,
        "description": "Fetch Discord guild roles and sync to database",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_DISCORD_ROLES_HOURS",
    },
    "warn_expiring_verifications": {
        "task": warn_expiring_verifications,
        "description": "Send DMs for expiring verifications",
        "scheduled": True,
        "hours_setting": "SCHEDULER_WARN_EXPIRING_VERIFICATIONS_HOURS",
        "kwargs": {"days": 15, "dry_run": False},
    },
    "sync_new_member_roles": {
        "task": sync_new_member_roles,
        "description": "Sync New Member Discord role based on guild join date",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_NEW_MEMBER_ROLES_HOURS",
    },
    "sync_data_connections": {
        "task": sync_all_data_connections,
        "description": "Sync all data connections with auto_sync enabled to Google Sheets",
        "scheduled": True,
        "hours_setting": "SCHEDULER_SYNC_DATA_CONNECTIONS_HOURS",
    },
    "purge_expired_api_keys": {
        "task": purge_expired_api_keys,
        "description": "Hard-delete user API keys that expired more than 90 days ago",
        "scheduled": True,
        "hours_setting": "SCHEDULER_PURGE_EXPIRED_API_KEYS_HOURS",
    },
}


def get_scheduled_tasks() -> list[dict[str, Any]]:
    """Return scheduled entries with their resolved interval (hours).

    Reads the Constance ``hours_setting`` for each scheduled task at call time, so
    a scheduler restart picks up edits made in the admin UI.

    Returns:
        List of dicts with ``id``, ``task``, ``description``, ``hours``, and
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
            "hours": getattr(config, info["hours_setting"]),
            "kwargs": info.get("kwargs", {}),
        })
    return jobs
