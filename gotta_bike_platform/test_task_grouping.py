"""Grouping of tasks and their cadences by the service each one contacts.

Two pages show the same twenty-seven-odd background jobs from different angles: one lists the
tasks and runs them, the other sets how often they run. Both are read the same way -- when
something is being hammered or has gone quiet, the useful question is which service it talks
to, not what the function is called -- so both group by that, from one mapping in the registry.

The render tests exist because configuration tests alone have already missed a real breakage
here: a template that raises at render time passes every check that does not actually render a
page, including ``manage.py check`` and any test that only inspects the context.
"""

import re

import pytest
from django.conf import settings
from django.urls import reverse

from gotta_bike_platform.task_registry import (
    SCHEDULER_COMPANION_SETTINGS,
    TASK_GROUP_ORDER,
    TASK_GROUPS,
    TASK_REGISTRY,
    group_rank,
    scheduler_setting_anchors,
    scheduler_setting_groups,
)


def test_every_group_in_the_registry_has_a_label_and_a_position():
    """A task grouped under a key nobody defined would render an empty heading."""
    for name, info in TASK_REGISTRY.items():
        group = info.get("group")
        assert group, f"{name} has no group"
        assert group in TASK_GROUPS, f"{name} is grouped under unknown key {group!r}"
        assert group in TASK_GROUP_ORDER, f"{group!r} has no position in TASK_GROUP_ORDER"


def test_every_scheduled_task_cadence_setting_maps_to_a_group():
    """The ratchet: a new scheduled task cannot land ungrouped on the scheduler page."""
    mapping = scheduler_setting_groups()
    for name, info in TASK_REGISTRY.items():
        if not info.get("scheduled"):
            continue
        setting = info.get("hours_setting") or info.get("minutes_setting")
        assert setting, f"{name} is scheduled but declares no cadence setting"
        assert setting in mapping, f"{name}'s cadence {setting} is not grouped"


def test_the_scheduler_fieldset_and_the_registry_agree():
    """A cadence setting missing from the fieldset is invisible; a stale one is a dead control."""
    fieldset = set(settings.CONSTANCE_CONFIG_FIELDSETS["Scheduler"])
    mapped = set(scheduler_setting_groups())
    assert not (mapped - fieldset), f"Cadence settings not shown on the page: {sorted(mapped - fieldset)}"
    for key in fieldset - mapped:
        # Anything left is a cadence-adjacent value that drives no task. Allowed, but it must
        # still be a real setting rather than a typo that silently renders nothing.
        assert key in settings.CONSTANCE_CONFIG, f"{key} is in the fieldset but is not a setting"


def test_unknown_groups_sort_last_rather_than_first():
    """Ordering by an unrecognised label must not push it above the real groups."""
    assert group_rank("no such group") >= len(TASK_GROUP_ORDER) - 1
    first = TASK_GROUPS[TASK_GROUP_ORDER[0]]
    assert group_rank(first) < group_rank("no such group")


@pytest.mark.django_db
def test_the_scheduler_page_renders_with_group_headings(admin_authed_client):
    """Renders the real page: a template error here is invisible to configuration tests."""
    response = admin_authed_client.get(reverse("config_section_page", args=["scheduler"]))
    assert response.status_code == 200

    body = response.content.decode()
    for group in scheduler_setting_groups().values():
        assert group in body, f"Group heading {group!r} missing from the scheduler page"


@pytest.mark.django_db
def test_the_scheduler_page_still_shows_every_cadence(admin_authed_client):
    """Reordering must not drop a setting -- a missing one is an interval nobody can change."""
    response = admin_authed_client.get(reverse("config_section_page", args=["scheduler"]))
    body = response.content.decode()

    for key in settings.CONSTANCE_CONFIG_FIELDSETS["Scheduler"]:
        assert key in body, f"{key} disappeared from the scheduler page"


@pytest.mark.django_db
def test_settings_are_ordered_so_each_group_appears_once(admin_authed_client):
    """Headings are emitted on change, so an unsorted list would repeat them down the page."""
    from apps.accounts.views import _get_config_sections

    ordered = _get_config_sections()["scheduler"]["settings"]
    starts = [s["group_label"] for s in ordered if s["group_start"]]
    assert len(starts) == len(set(starts)), f"A group is split across the page: {starts}"
    assert [s["group_label"] for s in ordered] == sorted(
        [s["group_label"] for s in ordered], key=lambda label: starts.index(label)
    ), "Settings are not contiguous within their group"


@pytest.mark.django_db
def test_other_sections_are_untouched_by_the_grouping(admin_authed_client):
    """Only the scheduler section is grouped; the rest must keep their fieldset order."""
    from apps.accounts.views import _get_config_sections

    sections = _get_config_sections()
    for key, section in sections.items():
        if key == "scheduler":
            continue
        for setting in section["settings"]:
            assert "group_label" not in setting, f"{key}.{setting['key']} was unexpectedly grouped"

    fieldsets = settings.CONSTANCE_CONFIG_FIELDSETS
    for section_name, keys in fieldsets.items():
        section_key = section_name.lower().replace(" ", "_")
        if section_key == "scheduler" or section_key not in sections:
            continue
        rendered = [s["key"] for s in sections[section_key]["settings"]]
        assert rendered == [k for k in keys if k in rendered], f"{section_name} was reordered"


@pytest.mark.django_db
def test_the_background_tasks_page_renders_with_its_groups(admin_authed_client):
    """The other half of the same mapping, rendered for real for the same reason."""
    response = admin_authed_client.get(reverse("config_section_page", args=["background_tasks"]))
    assert response.status_code == 200

    body = response.content.decode()
    for group in TASK_GROUP_ORDER:
        assert TASK_GROUPS[group] in body, f"Group heading {TASK_GROUPS[group]!r} missing from tasks page"


@pytest.mark.django_db
def test_saving_the_section_returns_a_still_grouped_partial(admin_authed_client):
    """The save re-renders the section, so the headings have to survive the round trip."""
    keys = settings.CONSTANCE_CONFIG_FIELDSETS["Scheduler"]
    posted = dict.fromkeys(keys, "7")

    response = admin_authed_client.post(
        reverse("config_section_update", args=["scheduler"]), posted, headers={"hx-request": "true"}
    )
    assert response.status_code == 200

    body = response.content.decode()
    for group in scheduler_setting_groups().values():
        assert group in body, f"Group heading {group!r} lost after saving"
    for key in keys:
        assert key in body, f"{key} lost after saving"


def test_every_companion_setting_points_at_a_real_task_with_a_cadence():
    """A companion whose task vanished would silently fall back to sorting alphabetically."""
    anchors = scheduler_setting_anchors()
    for setting, task_name in SCHEDULER_COMPANION_SETTINGS.items():
        assert task_name in TASK_REGISTRY, f"{setting} points at unknown task {task_name!r}"
        assert setting in anchors, f"{setting}'s task {task_name} has no cadence to sit beside"
        assert setting in settings.CONSTANCE_CONFIG, f"{setting} is not a real setting"


@pytest.mark.django_db
def test_a_companion_setting_renders_directly_after_its_cadence():
    """The wording of a threshold refers to the interval above it, so the order carries meaning."""
    from apps.accounts.views import _get_config_sections

    keys = [s["key"] for s in _get_config_sections()["scheduler"]["settings"]]
    for setting, cadence in scheduler_setting_anchors().items():
        assert keys.index(setting) == keys.index(cadence) + 1, (
            f"{setting} should sit immediately after {cadence}, got {keys}"
        )


def _section_html(client, section_key):
    """Fetch a config section page and return just the settings form.

    Scoped to the form because the surrounding chrome carries its own markup, and an assertion
    against the whole page would quietly be testing base.html too.

    Args:
        client: A logged-in test client.
        section_key: The config section to fetch.

    Returns:
        The form's HTML.

    """
    body = client.get(reverse("config_section_page", args=[section_key])).content.decode()
    start = body.index("<form hx-post")
    return body[start : body.index("</form>", start)]


@pytest.mark.django_db
def test_the_group_fieldsets_are_balanced(admin_authed_client):
    """Groups open and close across a template loop, where an unclosed tag breaks the layout."""
    form = _section_html(admin_authed_client, "scheduler")
    opened = form.count("<fieldset")
    closed = form.count("</fieldset>")

    assert opened == closed, f"{opened} fieldsets opened, {closed} closed"
    assert opened == len(set(scheduler_setting_groups().values())), "One fieldset per group expected"


@pytest.mark.django_db
def test_an_ungrouped_section_opens_no_fieldset(admin_authed_client):
    """The close is conditional on a group label, so an ungrouped section must open none."""
    form = _section_html(admin_authed_client, "site_settings")

    assert "<fieldset" not in form
    assert "</fieldset>" not in form


@pytest.mark.django_db
def test_each_group_legend_names_the_service(admin_authed_client):
    """The legend is what associates the controls with their service for a screen reader."""
    form = _section_html(admin_authed_client, "scheduler")
    blocks = re.findall(r"<legend[^>]*>(.*?)</legend>", form, re.S)
    legends = {re.sub(r"<[^>]+>", "", block).strip() for block in blocks}

    assert legends == set(scheduler_setting_groups().values()), f"Legends on the page: {sorted(legends)}"
