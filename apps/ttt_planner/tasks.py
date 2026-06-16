"""Background tasks for the TTT planner (zwiftgopher optimize)."""

from __future__ import annotations

import time

import logfire
from django.core.cache import cache
from django.tasks import task  # ty:ignore[unresolved-import]
from django.utils import timezone

from apps.ttt_planner.models import TttPlan
from apps.ttt_planner.services import zwiftgopher, zwiftgopher_client

# The API allows 1 request / 60 s per key+IP; we share one key, so throttle globally.
THROTTLE_KEY = "ttt:zwiftgopher:last_call_ts"
MIN_INTERVAL_S = 60


def _respect_rate_limit() -> None:
    """Sleep if needed so we don't exceed 1 zwiftgopher call per 60 s.

    Best-effort across processes via the Django cache; the API's own 429 is the
    real backstop.
    """
    last = cache.get(THROTTLE_KEY)
    now = time.time()
    if last is not None:
        wait = MIN_INTERVAL_S - (now - last)
        if wait > 0:
            time.sleep(min(wait, MIN_INTERVAL_S + 5))
    cache.set(THROTTLE_KEY, time.time(), timeout=MIN_INTERVAL_S * 2)


@task
def run_zwiftgopher_optimize(plan_id: str, route_schedule: str = zwiftgopher.DEFAULT_ROUTE_SCHEDULE) -> None:
    """Run the zwiftgopher optimizer for a plan and store the result on it.

    Args:
        plan_id: The plan UUID (string).
        route_schedule: One of ``next`` / ``next_wtrl`` / ``next_zrl``.

    """
    try:
        plan = TttPlan.objects.get(pk=plan_id)
    except TttPlan.DoesNotExist:
        logfire.warning("zwiftgopher task: plan not found", plan_id=plan_id)
        return

    def _save(status: str, *, error: str = "", result: dict | None = None) -> None:
        plan.zwiftgopher_status = status
        plan.zwiftgopher_error = error[:300]
        if result is not None:
            plan.zwiftgopher_result = result
        if status in (TttPlan.GopherStatus.DONE, TttPlan.GopherStatus.ERROR):
            plan.zwiftgopher_fetched_at = timezone.now()
        plan.save(
            update_fields=[
                "zwiftgopher_status",
                "zwiftgopher_error",
                "zwiftgopher_result",
                "zwiftgopher_request",
                "zwiftgopher_raw_response",
                "zwiftgopher_fetched_at",
            ]
        )

    if not zwiftgopher_client.is_configured():
        plan.zwiftgopher_request = None
        plan.zwiftgopher_raw_response = None
        _save(TttPlan.GopherStatus.ERROR, error="zwiftgopher API key not configured")
        return

    rider_count = zwiftgopher.count_optimizable_riders(plan)
    if rider_count < zwiftgopher.MIN_RIDERS:
        plan.zwiftgopher_request = None
        plan.zwiftgopher_raw_response = None
        _save(TttPlan.GopherStatus.ERROR, error=f"Need at least {zwiftgopher.MIN_RIDERS} riders with data")
        return

    with logfire.span("zwiftgopher optimize", plan_id=plan_id, riders=rider_count):
        _respect_rate_limit()
        payload = zwiftgopher.build_optimize_request(plan, route_schedule)
        status_code, data = zwiftgopher_client.optimize(payload)
        result = zwiftgopher.parse_optimize_response(status_code, data)
        plan.zwiftgopher_request = payload
        plan.zwiftgopher_raw_response = data

    if result.get("ok"):
        _save(TttPlan.GopherStatus.DONE, result=result)
        logfire.info("zwiftgopher optimize done", plan_id=plan_id, route=result.get("route"))
    else:
        _save(TttPlan.GopherStatus.ERROR, error=result.get("error", "unknown error"))
        logfire.warning("zwiftgopher optimize failed", plan_id=plan_id, error=result.get("error"))
