"""Team kit status: tracked per rider, per kit, on User.team_kit.

The design choices worth pinning are the ones that protect data rather than display it:

* statuses are stored as stable keys, and the same state is worded differently for the rider
  ("What's a kit") and the team ("Unknown") -- storing wording would split one state into two;
* kits are keyed by a slug fixed at creation, so renaming a kit keeps every rider's status;
* a rider's save must never clobber a status the team set, whether by the select not being
  able to represent it or by a page that does not render the kit fields at all.
"""

import json
from types import SimpleNamespace

import pytest
from constance import config
from django.urls import reverse
from django.utils.html import escape

from apps.accounts.forms import ProfileForm
from apps.team.kits import (
    DEFAULT_STATUS,
    RIDER_CHOICES,
    field_name,
    kit_rows,
    rider_label,
    status_for,
)
from apps.team.models import KitStatus, TeamKit


@pytest.fixture
def kit(db) -> TeamKit:
    """Build an active kit.

    Returns:
        The kit.

    """
    return TeamKit.objects.create(name="2026 Race Kit", slug="race-2026")


@pytest.fixture
def rider(user_model):
    """Build a rider with a complete profile, so the profile form validates.

    Returns:
        The rider.

    """
    return user_model.objects.create_user(
        username="rider", email="rider@example.test", first_name="Ana", last_name="Rider",
        birth_year=1990, gender="female", timezone="UTC", country="US",
        trainer=json.loads(config.TRAINER_OPTIONS)[0],
        heartrate_monitor=json.loads(config.HEARTRATE_MONITOR_OPTIONS)[0],
        permission_overrides={"team_member": True},
    )


def _form_data(user, **over) -> dict:
    """Build valid profile form data from a rider's current values.

    Args:
        user: The rider.
        **over: Fields to override or add, e.g. a kit field.

    Returns:
        The POST-shaped data.

    """
    data = {
        "first_name": user.first_name, "last_name": user.last_name,
        "birth_year": str(user.birth_year), "gender": user.gender,
        "timezone": user.timezone, "country": str(user.country),
        "trainer": user.trainer, "heartrate_monitor": user.heartrate_monitor,
        "unit_preference": "metric", "dual_recording": "",
    }
    data.update(over)
    return data


# --- defaults and wording --------------------------------------------------------------


@pytest.mark.django_db
def test_a_rider_who_has_not_answered_reads_as_whats_a_kit(rider, kit):
    """The requested default -- and adding a kit needs no backfill to get it."""
    assert status_for(rider, kit) == KitStatus.UNKNOWN
    assert rider_label(status_for(rider, kit)) == "What's a kit"


def test_one_state_is_worded_for_whoever_is_looking():
    """"What's a kit" and "Unknown" are one state, not two -- the wording is per audience."""
    assert rider_label(KitStatus.UNKNOWN) == "What's a kit"
    assert KitStatus.UNKNOWN.label == "Unknown"
    assert rider_label(KitStatus.NEED) == "I need the kit"
    assert rider_label(KitStatus.HAVE) == "I have the kit"


def test_team_only_states_are_shown_to_the_rider_in_the_teams_words():
    """They have no rider wording, and the team's label is already what the rider should read."""
    assert rider_label(KitStatus.SUBMITTED) == "Submitted to Zwift"
    assert rider_label(KitStatus.COMPLETED) == "Completed by Zwift"


def test_riders_choose_from_exactly_three():
    """Submitted and Completed describe a Zwift order the rider cannot see into."""
    assert set(RIDER_CHOICES) == {KitStatus.UNKNOWN, KitStatus.NEED, KitStatus.HAVE}


@pytest.mark.django_db
def test_a_hand_edited_garbage_value_reads_as_the_default(rider, kit):
    """The field is JSON and editable by hand; a bad value must not crash a profile page."""
    rider.team_kit = {kit.slug: "definitely-not-a-status"}
    rider.save(update_fields=["team_kit"])

    assert status_for(rider, kit) == DEFAULT_STATUS


# --- the rider's form ------------------------------------------------------------------


@pytest.mark.django_db
def test_a_rider_can_set_their_own_status(rider, kit):
    """The rider-facing edit the feature is for."""
    form = ProfileForm(_form_data(rider, **{field_name(kit): KitStatus.NEED}), instance=rider)
    assert form.is_valid(), form.errors
    form.save()

    rider.refresh_from_db()
    assert rider.team_kit == {kit.slug: KitStatus.NEED}


@pytest.mark.django_db
def test_a_rider_cannot_set_a_team_only_status(rider, kit):
    """The select is the server-side gate, not a hint: a crafted POST must not escalate."""
    form = ProfileForm(_form_data(rider, **{field_name(kit): KitStatus.COMPLETED}), instance=rider)

    assert not form.is_valid()
    assert field_name(kit) in form.errors
    rider.refresh_from_db()
    assert rider.team_kit == {}


@pytest.mark.django_db
def test_a_team_set_status_survives_an_unrelated_rider_save(rider, kit):
    """The bug this design exists to prevent.

    The team marks the kit "Submitted to Zwift". The rider later changes only their timezone
    and saves. Without the team's status offered as an option, the select could not
    represent it and the save would silently move the kit back to one of the rider's three.
    """
    rider.team_kit = {kit.slug: KitStatus.SUBMITTED}
    rider.save(update_fields=["team_kit"])

    unchanged = ProfileForm(instance=rider).initial.get(field_name(kit)) or KitStatus.SUBMITTED
    form = ProfileForm(
        _form_data(rider, timezone="Europe/London", **{field_name(kit): unchanged}), instance=rider
    )
    assert form.is_valid(), form.errors
    form.save()

    rider.refresh_from_db()
    assert rider.timezone == "Europe/London"
    assert rider.team_kit == {kit.slug: KitStatus.SUBMITTED}


@pytest.mark.django_db
def test_the_team_set_status_is_offered_and_labelled_as_such(rider, kit):
    """So the rider can see what the team set, and knows they did not set it themselves."""
    rider.team_kit = {kit.slug: KitStatus.SUBMITTED}
    rider.save(update_fields=["team_kit"])

    choices = dict(ProfileForm(instance=rider).fields[field_name(kit)].choices)

    assert choices[KitStatus.SUBMITTED] == "Submitted to Zwift (set by the team)"
    assert KitStatus.COMPLETED not in choices  # only the CURRENT team status is grandfathered


@pytest.mark.django_db
def test_a_save_that_does_not_send_the_kit_field_leaves_it_alone(rider, kit):
    """Absent from the POST means "untouched", never "reset to the default".

    Two pages post to the profile form. If either ever stops rendering the kit fields, a
    rider saving there must not wipe a status they -- or the team -- set.
    """
    rider.team_kit = {kit.slug: KitStatus.HAVE}
    rider.save(update_fields=["team_kit"])

    form = ProfileForm(_form_data(rider, timezone="Europe/London"), instance=rider)  # no kit field
    assert form.is_valid(), form.errors
    form.save()

    rider.refresh_from_db()
    assert rider.team_kit == {kit.slug: KitStatus.HAVE}


@pytest.mark.django_db
def test_only_submitted_fields_are_applied_even_if_cleaned_data_has_a_value(rider, kit):
    """The "absent means untouched" rule, isolated from the one that usually backs it up.

    Through a normal form an absent field cleans to "" and is dropped as an invalid status
    anyway, so the form-level test above cannot tell which of the two guards did the work.
    This one hands apply_kit_fields a real status that was never submitted -- the shape a
    future caller or a form subclass filling cleaned_data from initial would produce -- and
    only the submitted-field check stops it.
    """
    from apps.team.kits import apply_kit_fields

    rider.team_kit = {kit.slug: KitStatus.HAVE}

    changed = apply_kit_fields(rider, data={}, cleaned_data={field_name(kit): KitStatus.NEED}, kits=[kit])

    assert changed is False
    assert rider.team_kit == {kit.slug: KitStatus.HAVE}


@pytest.mark.django_db
def test_a_retired_kits_status_is_kept(rider, kit):
    """Retiring a kit hides it; it must not erase what riders recorded against it."""
    retired = TeamKit.objects.create(name="2024 Kit", slug="kit-2024", active=False)
    rider.team_kit = {retired.slug: KitStatus.HAVE}
    rider.save(update_fields=["team_kit"])

    form = ProfileForm(_form_data(rider, **{field_name(kit): KitStatus.NEED}), instance=rider)
    assert form.is_valid(), form.errors
    form.save()

    rider.refresh_from_db()
    assert rider.team_kit == {retired.slug: KitStatus.HAVE, kit.slug: KitStatus.NEED}


@pytest.mark.django_db
def test_renaming_a_kit_keeps_every_riders_status(rider, kit):
    """Keyed by the slug, which is fixed -- keying by name would have orphaned them all."""
    rider.team_kit = {kit.slug: KitStatus.HAVE}
    rider.save(update_fields=["team_kit"])

    kit.name = "Renamed Race Kit"
    kit.save()

    assert status_for(rider, kit) == KitStatus.HAVE


# --- the profile page ------------------------------------------------------------------


@pytest.mark.django_db
def test_the_public_profile_shows_the_status_in_the_header(client, team_member, rider, kit):
    """The requested display: the kit and where it stands, at the top of the profile."""
    rider.team_kit = {kit.slug: KitStatus.NEED}
    rider.save(update_fields=["team_kit"])
    client.force_login(team_member)

    body = client.get(reverse("accounts:public_profile", args=[rider.pk])).content.decode()
    header = body[: body.index("<!-- Info Cards -->")]

    assert "2026 Race Kit" in header
    assert "I need the kit" in header


@pytest.mark.django_db
def test_no_kits_defined_shows_nothing(client, team_member, rider):
    """Before any kit is configured there is nothing to say -- no empty heading."""
    client.force_login(team_member)

    body = client.get(reverse("accounts:public_profile", args=[rider.pk])).content.decode()

    assert "Team Kit" not in body
    assert kit_rows(rider) == []


@pytest.mark.django_db
def test_the_edit_page_offers_the_riders_three_choices(client, rider, kit):
    """The editor is on the page riders actually use to change their profile."""
    client.force_login(rider)

    body = client.get(reverse("accounts:profile_edit")).content.decode()

    assert f'name="{field_name(kit)}"' in body
    # Compared escaped: Django renders the apostrophe in "What's a kit" as &#x27;.
    for label in ("What's a kit", "I need the kit", "I have the kit"):
        assert escape(label) in body
    assert "Completed by Zwift" not in body


@pytest.mark.django_db
def test_saving_through_the_real_view_persists_the_status(client, rider, kit):
    """End to end through profile_edit, the path both profile pages post to."""
    client.force_login(rider)

    client.post(reverse("accounts:profile_edit"), _form_data(rider, **{field_name(kit): KitStatus.HAVE}))

    rider.refresh_from_db()
    assert rider.team_kit == {kit.slug: KitStatus.HAVE}


# --- the admin -------------------------------------------------------------------------


@pytest.fixture
def staff(user_model):
    """Build a superuser who can reach the Django admin.

    Returns:
        The staff user.

    """
    return user_model.objects.create_superuser(username="staff", email="staff@example.test", password=None)


@pytest.mark.django_db
def test_the_user_admin_offers_all_five_statuses_per_kit(client, staff, rider, kit):
    """The team's side: including the two a rider cannot set.

    Rendering the real change page is the point. The kit selects are declared on the form
    CLASS because the admin builds the form from fieldset names and rejects any it does not
    recognise -- a mistake there is a 500 on every user's change page, not a missing field.
    """
    client.force_login(staff)

    response = client.get(reverse("admin:accounts_user_change", args=[rider.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert f'name="{field_name(kit)}"' in body
    for status in KitStatus:
        assert status.label in body


@pytest.mark.django_db
def test_the_user_add_page_still_works(client, staff, kit):
    """No user exists yet, so there is no kit status to edit -- and the page must not break."""
    client.force_login(staff)

    response = client.get(reverse("admin:accounts_user_add"))

    assert response.status_code == 200
    assert field_name(kit) not in response.content.decode()


@pytest.mark.django_db
def test_the_user_admin_save_merges_the_team_status(rf, staff, rider, kit):
    """Setting "Submitted to Zwift" from the admin lands in team_kit, keeping other kits."""
    from django.contrib.admin.sites import site

    from apps.accounts.models import User

    other = TeamKit.objects.create(name="Casual Kit", slug="casual")
    rider.team_kit = {other.slug: KitStatus.HAVE}
    rider.save(update_fields=["team_kit"])

    request = rf.post("/admin/")
    request.user = staff

    submitted = {field_name(kit): KitStatus.SUBMITTED}
    form = SimpleNamespace(data=submitted, cleaned_data=submitted, team_kits=[kit])

    site._registry[User].save_model(request, rider, form, change=True)

    rider.refresh_from_db()
    assert rider.team_kit == {other.slug: KitStatus.HAVE, kit.slug: KitStatus.SUBMITTED}


@pytest.mark.django_db
def test_a_kits_slug_is_frozen_once_it_exists(rf, staff, kit):
    """The slug is every rider's key for this kit; changing it would orphan them all."""
    from django.contrib.admin.sites import site

    request = rf.get("/admin/")
    request.user = staff
    kit_admin = site._registry[TeamKit]

    assert "slug" in kit_admin.get_readonly_fields(request, kit)
    assert "slug" not in kit_admin.get_readonly_fields(request, None)
    assert kit_admin.get_prepopulated_fields(request, None) == {"slug": ("name",)}
    assert kit_admin.get_prepopulated_fields(request, kit) == {}


@pytest.mark.django_db
def test_the_team_kit_admin_pages_render(client, staff, kit):
    """Where kits are added -- the configuration half of the request."""
    client.force_login(staff)

    assert client.get(reverse("admin:team_teamkit_changelist")).status_code == 200
    assert client.get(reverse("admin:team_teamkit_add")).status_code == 200
    assert client.get(reverse("admin:team_teamkit_change", args=[kit.pk])).status_code == 200
