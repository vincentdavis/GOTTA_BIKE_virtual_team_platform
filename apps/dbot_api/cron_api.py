"""Cron API endpoints for triggering scheduled tasks."""

from constance import config as constance_config
from django.http import HttpRequest
from ninja import NinjaAPI
from ninja.security import APIKeyHeader

from apps.accounts.tasks import (
    guild_member_sync_status,
    sync_race_ready_roles,
    sync_youtube_channel_ids,
    sync_youtube_videos,
)
from apps.zwiftpower.tasks import update_team_results, update_team_riders
from apps.zwiftracing.tasks import sync_zr_riders


class CronAuth(APIKeyHeader):
    r"""API key authentication for cron jobs.

    Uses the same DBOT_AUTH_KEY for simplicity. You can configure a separate
    CRON_AUTH_KEY in constance if you want different keys for bot vs cron.

    Required header::

        X-Cron-Key: <DBOT_AUTH_KEY value>

    Example:
        curl -X POST -H "X-Cron-Key: your-secret-key" \
             http://localhost:8000/api/cron/task/update_team_riders

    """

    param_name = "X-Cron-Key"

    def authenticate(self, request: HttpRequest, key: str | None) -> dict | None:
        """Authenticate request using API key.

        Args:
            request: The HTTP request.
            key: The API key from the header.

        Returns:
            Dict with auth info if valid, None otherwise.

        """
        # Use the same key as the bot API for simplicity
        if not constance_config.DBOT_AUTH_KEY or key != constance_config.DBOT_AUTH_KEY:
            return None

        return {"api_key": key, "source": "cron"}


# Registry of available tasks
TASK_REGISTRY: dict = {
    "update_team_riders": {
        "task": update_team_riders,
        "description": "Fetch team riders from ZwiftPower",
    },
    "update_team_results": {
        "task": update_team_results,
        "description": "Fetch team results from ZwiftPower",
    },
    "sync_zr_riders": {
        "task": sync_zr_riders,
        "description": "Sync riders from Zwift Racing API",
    },
    "guild_member_sync_status": {
        "task": guild_member_sync_status,
        "description": "Check guild member sync health (actual sync done by Discord bot)",
    },
    "sync_race_ready_roles": {
        "task": sync_race_ready_roles,
        "description": "Sync race ready Discord roles for all users based on verification status",
    },
    "sync_youtube_channel_ids": {
        "task": sync_youtube_channel_ids,
        "description": "Extract YouTube channel IDs from user YouTube URLs",
    },
    "sync_youtube_videos": {
        "task": sync_youtube_videos,
        "description": "Fetch new videos from YouTube RSS feeds for all users",
    },
}


cron_api = NinjaAPI(
    auth=CronAuth(),
    urls_namespace="cron_api",
    title="Cron API",
    description="API endpoints for triggering scheduled tasks via cron",
)


@cron_api.get("/tasks")
def list_tasks(request: HttpRequest) -> dict:
    """List all available tasks that can be triggered.

    Args:
        request: The HTTP request.

    Returns:
        JSON object with available task names and descriptions.

    """
    return {
        "tasks": {name: info["description"] for name, info in TASK_REGISTRY.items()},
    }


@cron_api.post("/task/{task_name}")
def trigger_task(request: HttpRequest, task_name: str) -> dict:
    """Trigger a background task by name.

    Args:
        request: The HTTP request.
        task_name: The name of the task to trigger.

    Returns:
        JSON object with task status or error.

    """
    if task_name not in TASK_REGISTRY:
        return cron_api.create_response(
            request,
            {
                "error": "Task not found",
                "task_name": task_name,
                "available_tasks": list(TASK_REGISTRY.keys()),
            },
            status=404,
        )

    task_info = TASK_REGISTRY[task_name]
    task_func = task_info["task"]

    # Enqueue the task
    task_func.enqueue()

    return {
        "status": "queued",
        "task_name": task_name,
        "description": task_info["description"],
    }
