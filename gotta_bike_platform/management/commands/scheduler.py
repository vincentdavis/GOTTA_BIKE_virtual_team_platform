"""Management command to run the APScheduler-based task scheduler.

Replaces the external cron service by running scheduled tasks in-process.
Tasks are enqueued via django-tasks (db_worker still executes them).

To migrate a task from the external cron:
1. Add it to _get_scheduled_jobs() below
2. Add a SCHEDULER_*_HOURS Constance setting for its interval
3. Remove it from the external cron service's TASKS_TO_RUN list
4. Deploy both changes together
"""

import signal
import sys

import logfire
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from constance import config
from django.core.management.base import BaseCommand

# --- Default interval ---
EVERY_6H = 6


def _get_scheduled_jobs() -> list[dict]:
    """Build the list of scheduled jobs, reading intervals from Constance.

    Returns:
        List of job dicts with task, id, hours, and description keys.

    """
    return [
        {
            "task": "apps.zwiftpower.tasks.update_team_riders",
            "id": "update_team_riders",
            "hours": config.SCHEDULER_UPDATE_TEAM_RIDERS_HOURS,
            "description": "Fetch team riders from ZwiftPower",
        },
        {
            "task": "apps.zwiftpower.tasks.update_team_results",
            "id": "update_team_results",
            "hours": config.SCHEDULER_UPDATE_TEAM_RESULTS_HOURS,
            "description": "Fetch team results from ZwiftPower",
        },
        {
            "task": "apps.zwiftracing.tasks.sync_zr_riders",
            "id": "sync_zr_riders",
            "hours": config.SCHEDULER_SYNC_ZR_RIDERS_HOURS,
            "description": "Sync riders from Zwift Racing API",
        },
        # {
        #     "task": "apps.accounts.tasks.guild_member_sync_status",
        #     "id": "guild_member_sync_status",
        #     "hours": EVERY_6H,
        #     "description": "Check guild member sync health",
        # },
        {
            "task": "apps.accounts.tasks.refresh_all_race_ready",
            "id": "refresh_all_race_ready",
            "hours": config.SCHEDULER_REFRESH_ALL_RACE_READY_HOURS,
            "description": "Refresh cached is_race_ready",
        },
        # {
        #     "task": "apps.accounts.tasks.sync_race_ready_roles",
        #     "id": "sync_race_ready_roles",
        #     "hours": EVERY_6H,
        #     "description": "Sync race ready Discord roles",
        # },
        {
            "task": "apps.accounts.tasks.sync_youtube_channel_ids",
            "id": "sync_youtube_channel_ids",
            "hours": config.SCHEDULER_SYNC_YOUTUBE_CHANNEL_IDS_HOURS,
            "description": "Extract YouTube channel IDs",
        },
        {
            "task": "apps.accounts.tasks.sync_youtube_videos",
            "id": "sync_youtube_videos",
            "hours": config.SCHEDULER_SYNC_YOUTUBE_VIDEOS_HOURS,
            "description": "Fetch YouTube videos",
        },
        {
            "task": "apps.club_strava.tasks.sync_strava_activities",
            "id": "sync_strava_activities",
            "hours": config.SCHEDULER_SYNC_STRAVA_ACTIVITIES_HOURS,
            "description": "Fetch Strava activities",
        },
        # {
        #     "task": "apps.accounts.tasks.sync_zr_category_roles",
        #     "id": "sync_zr_category_roles",
        #     "hours": EVERY_6H,
        #     "description": "Sync ZR category Discord roles",
        # },
        {
            "task": "apps.team.tasks.sync_discord_channels",
            "id": "sync_discord_channels",
            "hours": config.SCHEDULER_SYNC_DISCORD_CHANNELS_HOURS,
            "description": "Sync Discord channels",
        },
        {
            "task": "apps.team.tasks.sync_discord_roles",
            "id": "sync_discord_roles",
            "hours": config.SCHEDULER_SYNC_DISCORD_ROLES_HOURS,
            "description": "Sync Discord roles",
        },
        {
            "task": "apps.team.tasks.warn_expiring_verifications",
            "id": "warn_expiring_verifications",
            "hours": config.SCHEDULER_WARN_EXPIRING_VERIFICATIONS_HOURS,
            "description": "Send DMs for expiring verifications",
            "kwargs": {"days": 15, "dry_run": False},
        },
    ]


def _enqueue_task(import_path: str, job_id: str, kwargs: dict | None = None) -> None:
    """Import a task function and enqueue it via django-tasks.

    Args:
        import_path: Dotted path to the task function.
        job_id: Identifier for logging.
        kwargs: Optional keyword arguments to pass to the task.

    """
    try:
        module_path, func_name = import_path.rsplit(".", 1)
        from importlib import import_module

        module = import_module(module_path)
        task_func = getattr(module, func_name)
        task_func.enqueue(**(kwargs or {}))
        logfire.info("Scheduler enqueued task", job_id=job_id)
    except Exception as e:
        logfire.error(
            "Scheduler failed to enqueue task",
            job_id=job_id,
            error=str(e),
        )


class Command(BaseCommand):
    """Run the APScheduler-based task scheduler."""

    help = "Start the background task scheduler (replaces external cron service)"

    def handle(self, *args, **options):
        """Start the scheduler and block until interrupted."""
        scheduler = BlockingScheduler()
        scheduled_jobs = _get_scheduled_jobs()

        active_count = 0
        for job in scheduled_jobs:
            scheduler.add_job(
                _enqueue_task,
                trigger=IntervalTrigger(hours=job["hours"]),
                args=[job["task"], job["id"], job.get("kwargs")],
                id=job["id"],
                name=job["description"],
                replace_existing=True,
            )
            active_count += 1
            self.stdout.write(
                f"  Registered: {job['id']} (every {job['hours']}h)"
            )

        if active_count == 0:
            self.stdout.write(self.style.WARNING(
                "No scheduled jobs enabled. "
                "Add entries in _get_scheduled_jobs() to activate."
            ))
            self.stdout.write(
                "Scheduler running (idle) - "
                "waiting for jobs to be enabled..."
            )
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Scheduler started with {active_count} job(s)."
            ))

        logfire.info("Scheduler started", active_jobs=active_count)

        # Graceful shutdown on SIGINT/SIGTERM
        def _shutdown(signum, frame):
            self.stdout.write("\nShutting down scheduler...")
            scheduler.shutdown(wait=False)
            sys.exit(0)

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        scheduler.start()
