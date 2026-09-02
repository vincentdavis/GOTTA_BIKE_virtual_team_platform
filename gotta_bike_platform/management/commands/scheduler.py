"""Management command to run the APScheduler-based task scheduler.

Replaces the external cron service by running scheduled tasks in-process.
Tasks are enqueued via django-tasks (db_worker still executes them).

The list of scheduled tasks lives in ``gotta_bike_platform.task_registry.TASK_REGISTRY``.
To schedule a new task: add an entry there with ``scheduled=True`` and a matching
``SCHEDULER_*_HOURS`` Constance setting -- or ``SCHEDULER_*_MINUTES`` via
``minutes_setting`` when the task needs finer granularity than an hour (also list it
in the ``Scheduler`` fieldset in ``settings.py``). Interval changes require a
scheduler restart to take effect.
"""

import signal
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import logfire
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from django.core.management.base import BaseCommand

from gotta_bike_platform.task_registry import get_scheduled_tasks


def _anchor_for(minutes: float) -> datetime:
    """Return a fixed wall-clock anchor so restarts do not move a job's slot.

    An IntervalTrigger with no start_date counts from the moment the scheduler booted, so a
    daily job fires at boot+24h. Every deploy re-anchors it, walking the slot forward and
    occasionally skipping a calendar day altogether -- which is how expiring-verification
    warnings were being missed.

    Anchoring to midnight UTC makes the schedule a property of the clock rather than of the
    last deploy: a job restarted at 15:00 still fires at its usual time, and the interval
    stays aligned across restarts.

    Args:
        minutes: The job's interval in minutes.

    Returns:
        A start_date in the past, aligned to midnight UTC.

    """
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    # Sub-daily jobs anchor to today's midnight; anything daily or slower to yesterday's, so
    # the first fire is never more than one interval away.
    return midnight if minutes < 24 * 60 else midnight - timedelta(days=1)


def _enqueue_task(task_func: Any, job_id: str, kwargs: dict | None = None) -> None:
    """Enqueue a task via django-tasks.

    Args:
        task_func: The ``@task``-decorated callable from the registry.
        job_id: Identifier for logging.
        kwargs: Optional keyword arguments to pass to the task.

    """
    try:
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
        scheduled_jobs = get_scheduled_tasks()

        active_count = 0
        for job in scheduled_jobs:
            minutes = job["minutes"]
            # APScheduler treats a non-positive interval as "run continuously", which
            # would hammer the queue and the upstream API. Refuse rather than obey.
            if minutes <= 0:
                logfire.error(
                    "Scheduler skipping job with a non-positive interval",
                    job_id=job["id"],
                    minutes=minutes,
                )
                self.stdout.write(self.style.ERROR(
                    f"  SKIPPED: {job['id']} (interval is {minutes} minutes - must be > 0)"
                ))
                continue

            scheduler.add_job(
                _enqueue_task,
                trigger=IntervalTrigger(minutes=minutes, start_date=_anchor_for(minutes)),
                args=[job["task"], job["id"], job.get("kwargs")],
                id=job["id"],
                name=job["description"],
                replace_existing=True,
                # A 24h job whose run is missed during a deploy should run on restart, not
                # wait another full day. The default grace is 1 second, which is why a daily
                # job could skip a calendar day entirely.
                misfire_grace_time=int(minutes * 60),
                coalesce=True,
            )
            active_count += 1
            cadence = f"{minutes / 60:g}h" if minutes >= 60 else f"{minutes:g}m"
            self.stdout.write(
                f"  Registered: {job['id']} (every {cadence})"
            )

        if active_count == 0:
            self.stdout.write(self.style.WARNING(
                "No scheduled jobs enabled. "
                "Add entries to TASK_REGISTRY (scheduled=True) to activate."
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
