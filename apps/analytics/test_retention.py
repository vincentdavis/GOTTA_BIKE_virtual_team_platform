"""Page-visit retention: anonymise, then delete.

Every figure on the analytics dashboard is computed live from these rows -- there is no
rollup -- so deleting them outright would throw away the history along with the personal
data. Anonymising first keeps the aggregate view and drops what identifies a person.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.analytics.models import PageVisit
from apps.analytics.tasks import purge_page_visits


def _visit(*, days_ago: int, user=None) -> PageVisit:
    """Create a page visit aged into the past.

    Returns:
        The visit.

    """
    visit = PageVisit.objects.create(
        user=user, ip_address="203.0.113.7", user_agent="Mozilla/5.0 (very identifying)",
        path="/events/12/", browser="Chrome", os="Android", device_type="mobile",
        timezone="Europe/London",
    )
    # timestamp is auto_now_add, so it has to be written after the fact.
    PageVisit.objects.filter(pk=visit.pk).update(
        timestamp=timezone.now() - timedelta(days=days_ago)
    )
    visit.refresh_from_db()
    return visit


@pytest.mark.django_db
def test_a_recent_visit_is_untouched(db) -> None:
    """Retention must not reach into the window the dashboard is normally showing."""
    visit = _visit(days_ago=10)

    purge_page_visits.func()

    visit.refresh_from_db()
    assert visit.ip_address == "203.0.113.7"


@pytest.mark.django_db
def test_an_old_visit_loses_what_identifies_a_person(db, team_member) -> None:
    """IP, account link and user-agent go; the row stays."""
    visit = _visit(days_ago=120, user=team_member)

    result = purge_page_visits.func()

    visit.refresh_from_db()
    assert visit.ip_address is None
    assert visit.user is None
    assert visit.user_agent == ""
    assert result["anonymised"] == 1


@pytest.mark.django_db
def test_the_aggregate_columns_survive_anonymising(db) -> None:
    """The whole point of anonymising rather than deleting."""
    visit = _visit(days_ago=120)

    purge_page_visits.func()

    visit.refresh_from_db()
    assert visit.path == "/events/12/"
    assert (visit.browser, visit.os, visit.device_type) == ("Chrome", "Android", "mobile")
    assert visit.timezone == "Europe/London"


@pytest.mark.django_db
def test_a_very_old_visit_is_deleted(db) -> None:
    """Anonymised rows still cost storage, so there is an outer limit."""
    visit = _visit(days_ago=800)

    result = purge_page_visits.func()

    assert not PageVisit.objects.filter(pk=visit.pk).exists()
    assert result["deleted"] == 1


@pytest.mark.django_db
def test_rerunning_does_not_rewrite_already_clean_rows(db) -> None:
    """Otherwise every run would rewrite the whole tail of the table."""
    _visit(days_ago=120)
    purge_page_visits.func()

    assert purge_page_visits.func()["anonymised"] == 0


@pytest.mark.django_db
def test_a_zero_window_disables_that_stage(db, settings) -> None:
    """Turning retention off has to actually leave the data alone."""
    from constance import config

    config.ANALYTICS_ANONYMISE_DAYS = 0
    visit = _visit(days_ago=400)

    result = purge_page_visits.func()

    visit.refresh_from_db()
    assert result["anonymised"] == 0
    assert visit.ip_address == "203.0.113.7"
    config.ANALYTICS_ANONYMISE_DAYS = 90
