"""Both planners' route pickers are searchable.

There are ~300 cycling routes, which is unusable as a flat <select>. Rather than a new
widget, both pickers opt into the shared `filter-select` enhancement already used by the
squad and event forms: it wraps the native <select>, which keeps holding and submitting
the value, and dispatches `change` on selection -- which matters here, because both
pickers use `onchange` to prefill the course name and terrain/profile.
"""

import pytest
from django.urls import reverse

from apps.ladder_planner.models import LadderMatchup
from apps.ttt_planner.models import TttPlan


@pytest.mark.django_db
def test_the_ttt_route_picker_is_searchable(auth_client, team_member) -> None:
    """The route select opts into the shared filter, and the script is on the page."""
    plan = TttPlan.objects.create(created_by=team_member, name="Mine")

    body = auth_client.get(reverse("ttt_planner:detail", args=[plan.pk])).content.decode()

    assert 'name="route"' in body
    assert "filter-select" in body
    assert "select.filter-select" in body  # the enhancement script itself


@pytest.mark.django_db
def test_the_ttt_route_picker_keeps_its_prefill_hook(auth_client, team_member) -> None:
    """The filter dispatches `change`, so this inline handler must still be wired.

    Losing it would silently stop the course name and terrain being prefilled on pick.
    """
    plan = TttPlan.objects.create(created_by=team_member, name="Mine")

    body = auth_client.get(reverse("ttt_planner:detail", args=[plan.pk])).content.decode()

    assert 'onchange="tttApplyRoute(this)"' in body


@pytest.mark.django_db
def test_the_ladder_route_picker_is_searchable(auth_client, team_member) -> None:
    """Same enhancement on the ladder matchup picker."""
    matchup = LadderMatchup.objects.create(created_by=team_member, name="Mine")

    body = auth_client.get(reverse("ladder_planner:detail", args=[matchup.pk])).content.decode()

    assert 'name="route"' in body
    assert "filter-select" in body
    assert "select.filter-select" in body


@pytest.mark.django_db
def test_the_ladder_route_picker_keeps_its_prefill_hook(auth_client, team_member) -> None:
    """Prefills the course name and profile."""
    matchup = LadderMatchup.objects.create(created_by=team_member, name="Mine")

    body = auth_client.get(reverse("ladder_planner:detail", args=[matchup.pk])).content.decode()

    assert 'onchange="ladderApplyRoute(this)"' in body
