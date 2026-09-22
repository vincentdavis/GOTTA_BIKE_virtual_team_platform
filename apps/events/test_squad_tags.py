"""Squad tags: the event's list is the only source, and each squad carries a subset of it.

Covers the rules module (``apps/events/squad_tags.py``), the event form that edits the list,
the squad form that picks from it, the pruning that follows an event save, and every page
that shows tags -- including that admin-typed tag text is escaped wherever it lands.
"""

import json
import re
import shutil
import subprocess  # noqa: S404 -- runs node on a page this test rendered
from datetime import date, timedelta
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.events.forms import EventForm, SquadForm
from apps.events.models import Event, EventSignup, Squad
from apps.events.squad_tags import (
    MAX_SQUAD_TAGS,
    MAX_TAG_LENGTH,
    clean_event_tags,
    normalize_tags,
    prune_squad_tags,
)

XSS_TAG = "<script>alert(1)</script>"
QUOTED_TAG = "Say \"hi\" & 'bye'"
NODE = shutil.which("node")
TEST_DATA = Path(__file__).parent / "test_data"


@pytest.fixture
def event(db) -> Event:
    """Build a visible event offering two squad tags.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL",
        start_date=today,
        end_date=today + timedelta(days=30),
        visible=True,
        squad_tags=["Red", "Blue"],
    )


@pytest.fixture
def untagged_event(db) -> Event:
    """Build a visible event with no squad tags.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="Untagged",
        start_date=today,
        end_date=today + timedelta(days=30),
        visible=True,
    )


def _squad_form(event, data=None, **kwargs) -> SquadForm:
    """Build a SquadForm wired the way the views wire it.

    Returns:
        The form, bound when ``data`` is given.

    """
    args = (data,) if data is not None else ()
    return SquadForm(
        *args,
        event_prefixes=event.prefixes or [],
        coordinator_role_ids=event.coordinator_role_ids or [],
        region_role_ids=event.region_role_ids or [],
        captain_role_ids=event.captain_role_ids or [],
        squad_tags=event.squad_tags or [],
        event=event,
        **kwargs,
    )


def _event_post(event, **extra) -> dict:
    """Build the minimum event edit POST.

    Returns:
        The POST data.

    """
    return {
        "title": event.title,
        "start_date": event.start_date.isoformat(),
        "end_date": event.end_date.isoformat(),
        "visible": "on",
        **extra,
    }


def _body(client, url: str) -> str:
    """GET a page and return its body.

    Returns:
        The decoded response body.

    """
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def _run_page_check(script: str, body: str, tmp_path) -> None:
    """Run one of the node checks in test_data/ on a rendered page, and fail on any BAD line.

    Each check runs the page's own inline script against test_data/mini_dom.js.
    """
    page = tmp_path / "page.html"
    page.write_text(body)
    result = subprocess.run(  # noqa: S603 -- fixed arguments, a page this test rendered
        [NODE, str(TEST_DATA / script), str(page)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BAD" not in result.stdout
    assert result.stdout.count("ok ") >= 20, result.stdout


def _form_post_data(form) -> dict:
    """Turn a form's current values into the POST a browser would send for it unchanged.

    Returns:
        The POST data.

    """
    data = {}
    for bound in form:
        if bound.field.disabled:
            continue
        value = bound.value()
        if value is None or value is False:
            continue
        if value is True:
            data[bound.html_name] = "on"
        elif isinstance(value, list | tuple):
            data[bound.html_name] = [str(item) for item in value]
        else:
            data[bound.html_name] = str(value)
    return data


def _admin_change_post(client, url: str) -> tuple[dict, dict]:
    """Read an admin change page and build the POST that would save it unchanged.

    Returns:
        The POST data, and the inline formsets' prefixes keyed by their model.

    """
    response = client.get(url)
    assert response.status_code == 200
    data = _form_post_data(response.context["adminform"].form)
    prefixes = {}
    for inline in response.context["inline_admin_formsets"]:
        formset = inline.formset
        prefixes[formset.model] = formset.prefix
        data.update(_form_post_data(formset.management_form))
        for form in formset.forms:
            data.update(_form_post_data(form))
    return data, prefixes


# --- normalize_tags / clean_event_tags ------------------------------------------------------


def test_normalize_strips_and_collapses_whitespace() -> None:
    assert normalize_tags(["  Red  ", "Tall\t  Squad", "Short\nOnes"]) == ["Red", "Tall Squad", "Short Ones"]


def test_normalize_dedupes_ignoring_case_and_keeps_the_first_spelling() -> None:
    assert normalize_tags(["Red", "RED", "Blue", "red", " blue "]) == ["Red", "Blue"]


def test_normalize_keeps_case_and_order() -> None:
    """Unlike the timezone chips, tags are not uppercased."""
    assert normalize_tags(["tall", "Blue", "RED"]) == ["tall", "Blue", "RED"]


def test_normalize_drops_empties() -> None:
    assert normalize_tags(["", "   ", "\t", "Red"]) == ["Red"]


def test_normalize_reads_a_malformed_value_as_no_tags() -> None:
    """A stored string must never be iterated character by character."""
    assert normalize_tags("Red") == []
    assert normalize_tags(None) == []
    assert normalize_tags(["Red", 3, None]) == ["Red"]


def test_clean_accepts_the_limits_exactly() -> None:
    at_limit = [f"T{i:0{MAX_TAG_LENGTH - 1}d}" for i in range(MAX_SQUAD_TAGS)]
    assert all(len(tag) == MAX_TAG_LENGTH for tag in at_limit)
    assert clean_event_tags(at_limit) == at_limit


def test_clean_refuses_a_tag_over_the_length_limit() -> None:
    with pytest.raises(ValidationError) as excinfo:
        clean_event_tags(["Red", "x" * (MAX_TAG_LENGTH + 1)])
    assert excinfo.value.code == "tag_too_long"
    assert str(MAX_TAG_LENGTH) in excinfo.value.messages[0]


def test_clean_refuses_too_many_tags() -> None:
    with pytest.raises(ValidationError) as excinfo:
        clean_event_tags([f"Tag {i}" for i in range(MAX_SQUAD_TAGS + 1)])
    assert excinfo.value.code == "too_many_tags"
    assert str(MAX_SQUAD_TAGS) in excinfo.value.messages[0]


def test_clean_counts_after_dedupe() -> None:
    """A case-duplicate does not count against the limit, since it is dropped."""
    tags = [f"Tag {i}" for i in range(MAX_SQUAD_TAGS)] + ["TAG 0"]
    assert len(clean_event_tags(tags)) == MAX_SQUAD_TAGS


@pytest.mark.parametrize("value", [{"Red": 1}, "Red", 3, ["Red", 3], [["Red"]]])
def test_clean_refuses_anything_but_a_list_of_strings(value) -> None:
    with pytest.raises(ValidationError) as excinfo:
        clean_event_tags(value)
    assert excinfo.value.code == "invalid_type"


def test_clean_reads_empty_as_no_tags() -> None:
    assert clean_event_tags(None) == []
    assert clean_event_tags("") == []
    assert clean_event_tags([]) == []


# --- EventForm --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_event_form_saves_normalised_tags(event) -> None:
    form = EventForm(_event_post(event, squad_tags='[" Red ", "red", "Tall  Squad", "Blue"]'), instance=event)
    assert form.is_valid(), form.errors
    form.save()
    event.refresh_from_db()
    assert event.squad_tags == ["Red", "Tall Squad", "Blue"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ("not json", "could not be read"),
        ('{"Red": 1}', "list of text labels"),
        ("[1, 2]", "list of text labels"),
        (f'["{"x" * (MAX_TAG_LENGTH + 1)}"]', f"at most {MAX_TAG_LENGTH} characters"),
        (
            "[" + ", ".join(f'"Tag {i}"' for i in range(MAX_SQUAD_TAGS + 1)) + "]",
            f"at most {MAX_SQUAD_TAGS} squad tags",
        ),
    ],
)
def test_event_form_refuses_bad_tag_lists(event, payload, fragment) -> None:
    form = EventForm(_event_post(event, squad_tags=payload), instance=event)
    assert not form.is_valid()
    assert fragment in " ".join(form.errors["squad_tags"])


@pytest.mark.django_db
def test_a_refused_list_leaves_the_event_alone(client, event, event_admin) -> None:
    client.force_login(event_admin)
    over = [f"Tag {i}" for i in range(MAX_SQUAD_TAGS + 1)]
    response = client.post(
        reverse("events:event_edit", args=[event.pk]),
        _event_post(event, squad_tags=json.dumps(over)),
    )
    assert response.status_code == 200  # re-rendered with the error, not redirected
    assert f"at most {MAX_SQUAD_TAGS} squad tags" in response.content.decode()
    event.refresh_from_db()
    assert event.squad_tags == ["Red", "Blue"]


@pytest.mark.django_db
def test_event_edit_page_renders_the_chip_editor(client, event, event_admin) -> None:
    client.force_login(event_admin)
    body = _body(client, reverse("events:event_edit", args=[event.pk]))
    hidden = re.search(r'<input[^>]*id="id_squad_tags"[^>]*>', body)
    assert hidden, "no squad_tags input"
    assert 'type="hidden"' in hidden.group(0)
    assert 'name="squad_tags"' in hidden.group(0)
    assert "<textarea" not in body.split('id="squad-tags-field"')[1].split("</div>")[0]
    assert '<label class="label" for="squad-tag-input">' in body
    assert 'id="squad-tag-input"' in body
    assert f'maxlength="{MAX_TAG_LENGTH}"' in body
    assert 'id="id_squad_tags_helptext"' in body
    assert "Removing a tag here removes it from every squad." in body


@pytest.mark.django_db
@pytest.mark.skipif(NODE is None, reason="needs node, as the Tailwind build does")
def test_the_chip_editor_script_adds_removes_and_keeps_a_typed_tag_on_save(client, event, event_admin, tmp_path):
    """Runs the edit page's own chip script -- see test_data/squad_tag_editor_check.js.

    Enter adds a tag without submitting the form, each remove button names its tag, and a tag
    typed but never added is saved with the event, or the save is stopped and says why.
    """
    client.force_login(event_admin)
    body = _body(client, reverse("events:event_edit", args=[event.pk]))
    _run_page_check("squad_tag_editor_check.js", body, tmp_path)


# --- prune_squad_tags -------------------------------------------------------------------------


@pytest.mark.django_db
def test_prune_drops_tags_the_event_no_longer_lists(event) -> None:
    squad = Squad.objects.create(event=event, name="A", tags=["Red", "Green"])
    assert prune_squad_tags(event) == 1
    squad.refresh_from_db()
    assert squad.tags == ["Red"]


@pytest.mark.django_db
def test_prune_rewrites_a_case_only_rename(event) -> None:
    squad = Squad.objects.create(event=event, name="A", tags=["Red"])
    event.squad_tags = ["RED", "Blue"]
    event.save(update_fields=["squad_tags"])
    prune_squad_tags(event)
    squad.refresh_from_db()
    assert squad.tags == ["RED"]


@pytest.mark.django_db
def test_prune_orders_tags_as_the_event_lists_them(event) -> None:
    squad = Squad.objects.create(event=event, name="A", tags=["Blue", "red"])
    prune_squad_tags(event)
    squad.refresh_from_db()
    assert squad.tags == ["Red", "Blue"]


@pytest.mark.django_db
def test_prune_writes_only_squads_that_change(event, untagged_event) -> None:
    unchanged = Squad.objects.create(event=event, name="A", tags=["Red", "Blue"])
    changed = Squad.objects.create(event=event, name="B", tags=["Green"])
    elsewhere = Squad.objects.create(event=untagged_event, name="C", tags=["Green"])
    before = Squad.objects.get(pk=unchanged.pk).updated_at
    changed_before = Squad.objects.get(pk=changed.pk).updated_at

    assert prune_squad_tags(event) == 1

    unchanged.refresh_from_db()
    changed.refresh_from_db()
    elsewhere.refresh_from_db()
    assert unchanged.tags == ["Red", "Blue"]
    assert unchanged.updated_at == before
    assert changed.tags == []
    # A rewritten squad really was modified, and the card's "Updated" line says so. The save
    # names updated_at explicitly, or its auto_now would not fire under update_fields.
    assert changed.updated_at > changed_before
    assert elsewhere.tags == ["Green"]  # another event's squads are not touched


@pytest.mark.django_db
def test_prune_logs_ids_and_counts_never_tag_text(event, monkeypatch) -> None:
    Squad.objects.create(event=event, name="A", tags=["Green"])
    calls = []
    monkeypatch.setattr("apps.events.squad_tags.logfire.info", lambda *a, **kw: calls.append((a, kw)))
    prune_squad_tags(event)
    assert calls == [(("Squad tags pruned to the event's list",), {"event_id": event.pk, "squads_changed": 1})]


@pytest.mark.django_db
def test_saving_the_event_prunes_its_squads(client, event, event_admin) -> None:
    squad = Squad.objects.create(event=event, name="A", tags=["Red", "Blue"])
    client.force_login(event_admin)
    response = client.post(
        reverse("events:event_edit", args=[event.pk]), _event_post(event, squad_tags='["blue", "Tall"]')
    )
    assert response.status_code == 302
    event.refresh_from_db()
    squad.refresh_from_db()
    assert event.squad_tags == ["blue", "Tall"]
    assert squad.tags == ["blue"]  # Red removed, Blue renamed to the event's new spelling


# --- SquadForm --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_squad_form_offers_only_the_event_tags(event) -> None:
    form = _squad_form(event)
    assert form.fields["tags"].choices == [("Red", "Red"), ("Blue", "Blue")]
    assert not form.fields["tags"].disabled


@pytest.mark.django_db
def test_squad_form_refuses_a_tampered_tag(event) -> None:
    form = _squad_form(event, {"name": "A", "gender": "COED", "tags": ["Red", "Purple"]})
    assert not form.is_valid()
    assert "tags" in form.errors


@pytest.mark.django_db
def test_squad_form_returns_picks_in_the_event_order(event) -> None:
    form = _squad_form(event, {"name": "A", "gender": "COED", "tags": ["Blue", "Red"]})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["tags"] == ["Red", "Blue"]


@pytest.mark.django_db
def test_squad_form_drops_a_stale_tag_from_initial(event) -> None:
    squad = Squad.objects.create(event=event, name="A", tags=["Gone", "blue"])
    form = _squad_form(event, instance=squad)
    # "Gone" is no longer offered; "blue" is shown in the event's spelling.
    assert form.initial["tags"] == ["Blue"]


@pytest.mark.django_db
def test_squad_form_is_disabled_when_the_event_has_no_tags(untagged_event) -> None:
    form = _squad_form(untagged_event)
    assert form.fields["tags"].disabled
    assert form.fields["tags"].choices == []

    bound = _squad_form(untagged_event, {"name": "A", "gender": "COED", "tags": ["Red"]})
    assert bound.is_valid(), bound.errors  # a disabled field ignores what is posted
    assert bound.cleaned_data["tags"] == []


@pytest.mark.django_db
def test_squad_form_page_explains_where_tags_come_from(client, untagged_event, event_admin) -> None:
    client.force_login(event_admin)
    body = _body(client, reverse("events:squad_create", args=[untagged_event.pk]))
    assert "<legend" in body
    assert "This event has no squad tags yet. An event admin can add them on the event" in body
    assert 'name="tags"' not in body


@pytest.mark.django_db
def test_squad_form_page_labels_each_tag_checkbox(client, event, event_admin) -> None:
    client.force_login(event_admin)
    body = _body(client, reverse("events:squad_create", args=[event.pk]))
    assert "<legend" in body
    for index, tag in enumerate(["Red", "Blue"]):
        assert f'for="id_tags_{index}"' in body
        assert f'value="{tag}"' in body
        assert f'<span class="label-text">{tag}</span>' in body


# --- squad create / edit views ----------------------------------------------------------------


@pytest.mark.django_db
def test_an_event_admin_creates_a_squad_with_tags(client, event, event_admin) -> None:
    client.force_login(event_admin)
    response = client.post(
        reverse("events:squad_create", args=[event.pk]), {"name": "A", "gender": "COED", "tags": ["Blue", "Red"]}
    )
    assert response.status_code == 302
    assert Squad.objects.get(event=event, name="A").tags == ["Red", "Blue"]


@pytest.mark.django_db
def test_a_squad_captain_who_is_not_an_event_admin_sets_tags(client, event, team_member) -> None:
    squad = Squad.objects.create(event=event, name="A", gender="COED")
    squad.captains.add(team_member)
    # The captain pickers list the event's registered riders, so re-posting the captain
    # needs them signed up; otherwise the save would also drop them as captain.
    EventSignup.objects.create(event=event, user=team_member, status=EventSignup.Status.REGISTERED)
    assert not team_member.is_event_admin
    client.force_login(team_member)

    response = client.post(
        reverse("events:squad_edit", args=[event.pk, squad.pk]),
        {"name": "A", "gender": "COED", "captains": [team_member.pk], "tags": ["Blue"]},
    )

    assert response.status_code == 302
    squad.refresh_from_db()
    assert squad.tags == ["Blue"]
    assert list(squad.captains.all()) == [team_member]


@pytest.mark.django_db
def test_a_tampered_tag_is_not_saved_by_the_edit_view(client, event, event_admin) -> None:
    squad = Squad.objects.create(event=event, name="A", gender="COED", tags=["Red"])
    client.force_login(event_admin)
    response = client.post(
        reverse("events:squad_edit", args=[event.pk, squad.pk]), {"name": "A", "gender": "COED", "tags": ["Purple"]}
    )
    assert response.status_code == 200  # re-rendered with the error
    squad.refresh_from_db()
    assert squad.tags == ["Red"]


@pytest.mark.django_db
def test_a_rider_who_cannot_edit_the_squad_cannot_tag_it(client, event, team_member) -> None:
    squad = Squad.objects.create(event=event, name="A", gender="COED")
    client.force_login(team_member)
    client.post(
        reverse("events:squad_edit", args=[event.pk, squad.pk]), {"name": "A", "gender": "COED", "tags": ["Red"]}
    )
    squad.refresh_from_db()
    assert squad.tags == []


# --- event detail: column, menu entry, filter --------------------------------------------------


@pytest.mark.django_db
def test_event_detail_renders_the_tags_column_menu_and_filter(client, event, event_admin) -> None:
    Squad.objects.create(event=event, name="A", tags=["Blue"])
    Squad.objects.create(event=event, name="B")
    client.force_login(event_admin)
    body = _body(client, reverse("events:event_detail", args=[event.pk]))

    assert '<th data-scol="sqf_tags" data-sort="text"' in body
    assert 'class="checkbox checkbox-xs squad-field-toggle" data-scol="sqf_tags"> Tags</label>' in body
    assert '<td data-scol="sqf_tags" class="whitespace-nowrap">' in body
    assert 'id="squad-tag-filter"' in body
    assert "Filter squads by tag" in body
    assert 'data-squad-tag-filter="" aria-pressed="true">All</button>' in body
    assert 'data-squad-tag-filter="Red" aria-pressed="false">Red</button>' in body
    assert 'data-squad-tag-filter="Blue" aria-pressed="false">Blue</button>' in body
    assert 'id="squad-tag-filter-status" role="status"' in body
    assert 'data-squad-tags="[&quot;Blue&quot;]"' in body
    assert 'data-squad-tags="[]"' in body  # the untagged squad matches no tag
    assert 'colspan="12"' in body
    # The Tags column is on by default but still honours a saved choice.
    assert "sqf_tags: true" in body


@pytest.mark.django_db
def test_event_detail_leaves_it_all_out_without_tags(client, untagged_event, event_admin) -> None:
    Squad.objects.create(event=untagged_event, name="A")
    client.force_login(event_admin)
    body = _body(client, reverse("events:event_detail", args=[untagged_event.pk]))

    assert 'data-scol="sqf_tags"' not in body
    assert 'id="squad-tag-filter"' not in body
    assert "Filter squads by tag" not in body
    assert 'data-squad-tags="' not in body
    assert 'colspan="11"' in body


@pytest.mark.django_db
@pytest.mark.skipif(NODE is None, reason="needs node, as the Tailwind build does")
def test_the_tag_filter_script_shows_hides_and_counts_squads(client, event, event_admin, tmp_path):
    """Runs the event page's own squads-table script -- see test_data/squad_tag_filter_check.js.

    One tag at a time, case-insensitive, untagged squads hidden, details rows hidden with
    their squad and kept with it by the sort, Expand all limited to what is shown, the
    status line, and the Tags column's default and saved choice.
    """
    event.squad_tags = ["Red", "Blue", "Tall"]
    event.save(update_fields=["squad_tags"])
    for name, tags in [("Delta", ["Red", "Blue"]), ("Charlie", []), ("Bravo", ["Blue"]), ("Alpha", ["Red"])]:
        Squad.objects.create(event=event, name=name, tags=tags)
    client.force_login(event_admin)
    body = _body(client, reverse("events:event_detail", args=[event.pk]))
    _run_page_check("squad_tag_filter_check.js", body, tmp_path)


# --- squad cards ------------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["squad_manage", "squad_assign_page"])
def test_squad_cards_show_the_tags(client, event, event_admin, url_name) -> None:
    Squad.objects.create(event=event, name="A", gender="Male", tags=["Red", "Blue"])
    client.force_login(event_admin)
    body = _body(client, reverse(f"events:{url_name}", args=[event.pk]))
    # A named list, so a screen reader hears where the tags end: the gender badge that
    # follows on the Assign Riders card is not read as one more tag.
    tag_list = re.search(r'<ul role="list" class="[^"]*" aria-label="Tags">(.*?)</ul>', body)
    assert tag_list, "no labelled tag list"
    assert re.findall(r"<li [^>]*>([^<]*)</li>", tag_list.group(1)) == ["Red", "Blue"]
    assert "Male" not in tag_list.group(1)


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["squad_manage", "squad_assign_page", "event_detail"])
def test_every_page_shows_the_same_tags_even_after_a_write_that_skipped_the_forms(
    client, event, event_admin, url_name
) -> None:
    """``Squad.tag_list`` prunes at render, so the three surfaces cannot disagree.

    Every UI write path prunes already; a shell ``update()``, a data migration or a fixture
    load does not. Without the render-time prune the cards would still show a dropped tag,
    in the squad's own spelling, that the event page had already stopped showing.
    """
    squad = Squad.objects.create(event=event, name="A")
    Squad.objects.filter(pk=squad.pk).update(tags=["Gone", "blue"])
    client.force_login(event_admin)

    body = _body(client, reverse(f"events:{url_name}", args=[event.pk]))

    assert "Gone" not in body
    assert re.search(r'<li class="badge[^"]*">Blue</li>', body), "no Blue badge"


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["squad_manage", "squad_assign_page", "event_detail"])
def test_a_long_tag_can_wrap_instead_of_widening_the_page(client, event, event_admin, url_name) -> None:
    """A 40-character tag is wider than a phone at the largest text size.

    On the cards the badge can wrap; in the scrolling table the cell keeps it on one line.
    """
    long_tag = "Thursday EMEA Women's Development Group"
    event.squad_tags = [long_tag]
    event.save(update_fields=["squad_tags"])
    Squad.objects.create(event=event, name="A", tags=[long_tag])
    client.force_login(event_admin)
    body = _body(client, reverse(f"events:{url_name}", args=[event.pk]))
    badges = re.findall(r'<li class="([^"]*)">Thursday EMEA Women&#x27;s Development Group</li>', body)
    assert badges
    for classes in badges:
        assert {"h-auto", "max-w-full", "wrap-anywhere"} <= set(classes.split())
        assert "whitespace-nowrap" not in classes.split()
    if url_name == "event_detail":
        button = re.search(r'<button type="button" class="([^"]*)" data-squad-tag-filter="Thursday EMEA', body).group(1)
        assert {"btn-xs", "h-auto", "min-h-[var(--size)]", "max-w-full", "wrap-anywhere"} <= set(button.split())


# --- escaping ---------------------------------------------------------------------------------


@pytest.fixture
def hostile_event(event) -> Event:
    """Give the event tags that would break out of HTML if rendered raw.

    Returns:
        The event, with one squad carrying both tags.

    """
    event.squad_tags = [XSS_TAG, QUOTED_TAG]
    event.save(update_fields=["squad_tags"])
    Squad.objects.create(event=event, name="A", tags=[XSS_TAG, QUOTED_TAG])
    return event


def _assert_escaped(body: str) -> None:
    """Fail if either hostile tag reached the page unescaped."""
    assert XSS_TAG not in body
    assert QUOTED_TAG not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "Say &quot;hi&quot; &amp; &#x27;bye&#x27;" in body


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["event_detail", "squad_manage", "squad_assign_page"])
def test_hostile_tags_are_escaped_on_event_pages(client, hostile_event, event_admin, url_name) -> None:
    client.force_login(event_admin)
    _assert_escaped(_body(client, reverse(f"events:{url_name}", args=[hostile_event.pk])))


@pytest.mark.django_db
def test_hostile_tags_are_escaped_in_the_event_form_json(client, hostile_event, event_admin) -> None:
    """The edit page carries the tags only as JSON in the hidden input; the chips are built with textContent."""
    client.force_login(event_admin)
    body = _body(client, reverse("events:event_edit", args=[hostile_event.pk]))
    assert XSS_TAG not in body
    assert QUOTED_TAG not in body
    assert (
        'value="[&quot;&lt;script&gt;alert(1)&lt;/script&gt;&quot;, '
        '&quot;Say \\&quot;hi\\&quot; &amp; &#x27;bye&#x27;&quot;]"'
    ) in body


@pytest.mark.django_db
def test_hostile_tags_are_escaped_on_the_squad_form(client, hostile_event, event_admin) -> None:
    squad = hostile_event.squads.get()
    client.force_login(event_admin)
    _assert_escaped(_body(client, reverse("events:squad_edit", args=[hostile_event.pk, squad.pk])))


@pytest.mark.django_db
def test_hostile_tags_are_escaped_inside_the_filter_data_attribute(client, hostile_event, event_admin) -> None:
    client.force_login(event_admin)
    body = _body(client, reverse("events:event_detail", args=[hostile_event.pk]))
    assert (
        'data-squad-tags="[&quot;&lt;script&gt;alert(1)&lt;/script&gt;&quot;, '
        '&quot;Say \\&quot;hi\\&quot; &amp; &#x27;bye&#x27;&quot;]"'
    ) in body


# --- Django admin -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_event_admin_prunes_squads_after_an_inline_squad_edit(client, event, superuser) -> None:
    """The squad inline saves its changed rows after save_model, from rows read before it.

    Pruning must come after, or the inline writes the removed tag straight back.
    """
    squad = Squad.objects.create(event=event, name="A", tags=["Red", "Blue"])
    client.force_login(superuser)
    url = reverse("admin:events_event_change", args=[event.pk])
    data, prefixes = _admin_change_post(client, url)
    data["squad_tags"] = '["Red"]'
    data[f"{prefixes[Squad]}-0-name"] = "A renamed"

    response = client.post(url, data)

    assert response.status_code == 302, response.context["adminform"].form.errors if response.context else ""
    squad.refresh_from_db()
    assert squad.name == "A renamed"
    assert squad.tags == ["Red"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("payload", "saved"),
    [('[" Red ", "red", "Blue"]', ["Red", "Blue"]), ("", []), ("null", [])],
)
def test_the_event_admin_normalises_squad_tags(client, event, superuser, payload, saved) -> None:
    client.force_login(superuser)
    url = reverse("admin:events_event_change", args=[event.pk])
    data, _ = _admin_change_post(client, url)
    data["squad_tags"] = payload

    response = client.post(url, data)

    assert response.status_code == 302
    event.refresh_from_db()
    assert event.squad_tags == saved


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload",
    ['"Red"', '{"Red": 1}', "[1, 2]", json.dumps(["x" * (MAX_TAG_LENGTH + 1)])],
)
def test_the_event_admin_refuses_what_the_edit_page_refuses(client, event, superuser, payload) -> None:
    """A JSON string would read as no tags and the prune would strip every squad."""
    squad = Squad.objects.create(event=event, name="A", tags=["Red"])
    client.force_login(superuser)
    url = reverse("admin:events_event_change", args=[event.pk])
    data, _ = _admin_change_post(client, url)
    data["squad_tags"] = payload

    response = client.post(url, data)

    assert response.status_code == 200
    assert "squad_tags" in response.context["adminform"].form.errors
    event.refresh_from_db()
    squad.refresh_from_db()
    assert event.squad_tags == ["Red", "Blue"]
    assert squad.tags == ["Red"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("payload", "saved"),
    [("", []), ("null", []), ('["blue", "Purple", "Red"]', ["Red", "Blue"])],
)
def test_the_squad_admin_saves_tags_held_to_the_event(client, event, superuser, payload, saved) -> None:
    """An emptied Tags box is no tags, not a NULL the column refuses with a 500."""
    squad = Squad.objects.create(event=event, name="A", tags=["Red"])
    client.force_login(superuser)
    url = reverse("admin:events_squad_change", args=[squad.pk])
    data, _ = _admin_change_post(client, url)
    data["tags"] = payload

    response = client.post(url, data)

    assert response.status_code == 302
    squad.refresh_from_db()
    assert squad.tags == saved


@pytest.mark.django_db
def test_the_squad_admin_refuses_tags_that_are_not_a_list(client, event, superuser) -> None:
    squad = Squad.objects.create(event=event, name="A", tags=["Red"])
    client.force_login(superuser)
    url = reverse("admin:events_squad_change", args=[squad.pk])
    data, _ = _admin_change_post(client, url)
    data["tags"] = '"Red"'

    response = client.post(url, data)

    assert response.status_code == 200
    assert "tags" in response.context["adminform"].form.errors
    squad.refresh_from_db()
    assert squad.tags == ["Red"]
