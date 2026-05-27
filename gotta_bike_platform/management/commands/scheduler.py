"""Management command to run the APScheduler-based task scheduler.

Replaces the external cron service by running scheduled tasks in-process.
Tasks are enqueued via django-tasks (db_worker still executes them).

The list of scheduled tasks lives in ``gotta_bike_platform.task_registry.TASK_REGISTRY``.
To schedule a new task: add an entry there with ``scheduled=True`` and a matching
``SCHEDULER_*_HOURS`` Constance setting (also list it in the ``Scheduler`` fieldset
in ``settings.py``). Interval changes require a scheduler restart to take effect.
"""

import signal
import sys
from typing import Any

import logfire
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from django.core.management.base import BaseCommand

from gotta_bike_platform.task_registry import get_scheduled_tasks


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
