"""Both planners' route pickers are searchable.

There are ~300 cycling routes, which is unusable as a flat <select>. Rather than a new
widget, both pickers opt into the shared `filter-select` enhancement already used by the
squad and event forms: it wraps the native <select>, which keeps holding and submitting
the value, and dispatches `change` on selection -- which matters here, because both
pickers use `onchange` to prefill the course name and terrain/profile.

The script must not put a Datastar attribute on the rows it builds: Datastar is loaded on
every page and evaluates them as expressions, which is how a `data-text="— No route —"`
filled the console with syntax errors.
"""

import re

import pytest
from django.template.loader import get_template
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


# The attribute plugins Datastar 1.0.2 (loaded in base.html) acts on. It takes the attribute name
# after "data-", up to the first ":" (a key) or "__" (modifiers), as the plugin name, exactly.
DATASTAR_ATTRIBUTES = (
    "attr",
    "bind",
    "class",
    "computed",
    "effect",
    "indicator",
    "init",
    "json-signals",
    "on",
    "on-intersect",
    "on-interval",
    "on-signal-patch",
    "ref",
    "show",
    "signals",
    "style",
    "text",
)


def test_the_filter_script_writes_no_attribute_datastar_acts_on() -> None:
    source = get_template("events/_filter_select_script.html").template.source
    as_dataset = "|".join(re.sub(r"-(\w)", lambda m: m.group(1).upper(), name) for name in DATASTAR_ATTRIBUTES)
    as_attribute = "|".join(DATASTAR_ATTRIBUTES)

    assert not re.search(rf"dataset\.({as_dataset})\b", source)
    assert not re.search(rf"""setAttribute\(\s*['"]data-({as_attribute})(['":]|__)""", source)
