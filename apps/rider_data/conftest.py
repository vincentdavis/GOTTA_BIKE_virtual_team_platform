"""Shared fixtures for rider_data tests."""

from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.utils import timezone


@pytest.fixture
def healthy_sync(db):
    """Record a recent successful sync so the purge's health guard lets it run.

    The guard refuses to delete when the sync has not succeeded inside the window, so any
    test exercising eviction has to say the sync is working.
    """
    django_apps.get_model("django_tasks_database", "DBTaskResult").objects.create(
        task_path="apps.rider_data.tasks.sync_rider_profiles",
        status="SUCCESSFUL",
        finished_at=timezone.now() - timedelta(minutes=5),
        args_kwargs={"args": [], "kwargs": {}},
        queue_name="default",
        backend_name="default",
        priority=0,
    )
