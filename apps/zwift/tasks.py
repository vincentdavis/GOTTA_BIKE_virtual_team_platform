"""Background tasks for the zwift app."""

from django.tasks import task  # ty:ignore[unresolved-import]

from apps.zwift import verification


@task
def sync_zauth_verifications() -> dict:
    """Reconcile all users' zauth verification status against the zwift service.

    Thin wrapper around :func:`apps.zwift.verification.reconcile_all`; scheduled
    hourly (``SCHEDULER_SYNC_ZAUTH_VERIFICATIONS_HOURS``) and manually triggerable
    from ``/site/config/background_tasks/``.

    Returns:
        The reconcile summary dict.

    """
    return verification.reconcile_all()
