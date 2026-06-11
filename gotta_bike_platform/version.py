"""Deployment build metadata surfaced in the UI header.

``DEPLOY_TIME`` is captured once when this module is first imported, which on
Railway is effectively the deploy time (each deploy starts a fresh container).
It can be overridden with a ``DEPLOY_TIMESTAMP`` env var (ISO 8601). The short
commit SHA comes from ``DEPLOY_VERSION`` or Railway's ``RAILWAY_GIT_COMMIT_SHA``
and is blank in local development.
"""

import os
from datetime import UTC, datetime

from django.utils import timezone


def _resolve_deploy_time() -> datetime:
    """Resolve the deploy timestamp from env override, else process-start time.

    Returns:
        A timezone-aware datetime (UTC if the override is naive).

    """
    override = os.environ.get("DEPLOY_TIMESTAMP", "").strip()
    if override:
        try:
            parsed = datetime.fromisoformat(override)
        except ValueError:
            return timezone.now()
        return parsed if timezone.is_aware(parsed) else parsed.replace(tzinfo=UTC)
    return timezone.now()


def _resolve_deploy_version() -> str:
    """Resolve the short commit SHA from env (blank locally).

    Returns:
        First 7 chars of the commit SHA, or an empty string.

    """
    sha = os.environ.get("DEPLOY_VERSION") or os.environ.get("RAILWAY_GIT_COMMIT_SHA") or ""
    return sha.strip()[:7]


DEPLOY_TIME = _resolve_deploy_time()
DEPLOY_VERSION = _resolve_deploy_version()
