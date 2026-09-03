"""Where the squad edit form puts the captain role, and where the squad card names captains.

Both are placement, which is exactly the kind of thing a normal test misses: assert only that
a name appears somewhere in the body and it passes whether the name is in the Profile block,
in a search-index attribute, or three cards further down. Every assertion here is scoped to
the region it is actually about.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events.models import Event, Squad


@pytest.fixture
def event(db) -> Event:
    """Build a visible event.

    Returns:
        The event.

    """
    today = date.today()
    return Event.objects.create(
        title="ZRL", start_date=today, end_date=today + timedelta(days=30), visible=True
    )


@pytest.fixture
def squad(event) -> Squad:
    """Build a squad with no leadership yet.

    Returns:
        The squad.

    """
    return Squad.objects.create(event=event, name="Eclipse", squad_timezone="Europe/London")


def _profile_section(body: str) -> str:
    """Slice out just the squad card's Profile block.

    The card renders several visually identical bg-base-300 blocks, and the captains' names
    also appear in the card's data-search attribute, so an unscoped assertion proves nothing
    about placement.

    Args:
        body: The rendered page.

    Returns:
        The Profile block's markup.

    """
    start = body.index(">Profile</p>")
    rest = body[start:]
    end = rest.find('class="card bg-base-300')
    section = rest[:end] if end != -1 else rest
    # Self-check: if the slice ever silently became "the rest of the page", every scoped
    # assertion below would start passing for the wrong reason.
    assert end != -1, "Profile block has no following card to bound it"
    assert "Timezone" in section
    assert len(section) < len(body) / 4
    return section


# --- the squad edit form ---------------------------------------------------------------


@pytest.mark.django_db
def test_captain_discord_role_sits_immediately_before_discord_channel(client, event_admin, squad):
    """It configures a Discord role, so it belongs with the other Discord fields, not up by the name."""
    client.force_login(event_admin)
    body = client.get(reverse("events:squad_edit", args=[squad.event_id, squad.pk])).content.decode()

    captain_role = body.index('for="id_discord_captain_role"')
    channel = body.index('for="id_discord_channel_id"')
    captains_picker = body.index('id_captains')

    assert captain_role < channel
    # Nothing else may sneak between the two.
    assert 'label-text">Discord Channel' in body[captain_role:channel + 200]
    # And it is no longer sitting up beside the squad name, above the captains picker.
    assert captains_picker < captain_role


@pytest.mark.django_db
def test_the_captain_role_help_text_does_not_point_the_wrong_way(client, event_admin, squad):
    """It used to say "chosen below"; after the move that direction is simply false."""
    client.force_login(event_admin)
    body = client.get(reverse("events:squad_edit", args=[squad.event_id, squad.pk])).content.decode()

    assert "captains and vice-captains chosen below" not in body
    assert "Captain Discord Role above" not in body


# --- the squad card --------------------------------------------------------------------


@pytest.mark.django_db
def test_the_profile_section_names_the_captains(client, event_admin, squad, user_model):
    """The most-asked question about a squad is who runs it; it should not need a click."""
    cap = user_model.objects.create_user(
        username="cap", email="cap@example.test", first_name="Ada", last_name="Racer"
    )
    squad.captains.add(cap)
    client.force_login(event_admin)
    body = client.get(reverse("events:squad_manage", args=[squad.event_id])).content.decode()

    assert "Ada Racer" in _profile_section(body)


@pytest.mark.django_db
def test_the_profile_section_names_the_vice_captains(client, event_admin, squad, user_model):
    """Same question, same block -- a vice-captain is who you ask when the captain is racing."""
    vc = user_model.objects.create_user(
        username="vc", email="vc@example.test", first_name="Bo", last_name="Vice"
    )
    squad.vice_captains.add(vc)
    client.force_login(event_admin)
    body = client.get(reverse("events:squad_manage", args=[squad.event_id])).content.decode()

    assert "Bo Vice" in _profile_section(body)


@pytest.mark.django_db
def test_every_captain_is_listed_not_just_the_first(client, event_admin, squad, user_model):
    """Squads run by a pair are normal; showing one of them silently is worse than showing none."""
    for i, name in enumerate(("Ada", "Bo")):
        squad.captains.add(
            user_model.objects.create_user(
                username=f"cap{i}", email=f"cap{i}@example.test", first_name=name, last_name="Racer"
            )
        )
    client.force_login(event_admin)
    section = _profile_section(
        client.get(reverse("events:squad_manage", args=[squad.event_id])).content.decode()
    )

    assert "Ada Racer" in section
    assert "Bo Racer" in section


@pytest.mark.django_db
def test_a_squad_with_no_captain_shows_the_row_anyway(client, event_admin, squad):
    """On a management page, the squad missing a captain is the one you are looking for."""
    client.force_login(event_admin)
    section = _profile_section(
        client.get(reverse("events:squad_manage", args=[squad.event_id])).content.decode()
    )

    assert "Captain" in section
    assert "Vice Captain" in section
    assert "—" in section


@pytest.mark.django_db
def test_the_names_are_not_also_left_in_a_second_card(client, event_admin, squad, user_model):
    """The old Leadership card was folded in, not copied -- the same names twice reads as a bug."""
    cap = user_model.objects.create_user(
        username="cap", email="cap@example.test", first_name="Ada", last_name="Racer"
    )
    squad.captains.add(cap)
    client.force_login(event_admin)
    body = client.get(reverse("events:squad_manage", args=[squad.event_id])).content.decode()

    assert ">Leadership</p>" not in body
    # Once in the Profile block, once in the card's lowercased data-search attribute.
    assert body.count("Ada Racer") == 1


@pytest.mark.django_db
def test_a_captain_without_a_real_name_falls_back_to_discord(client, event_admin, squad, user_model):
    """Most riders here have no first/last name set; a blank row would be the common case."""
    cap = user_model.objects.create_user(username="lurker", email="lurker@example.test")
    cap.discord_username = "shadowrider"
    cap.save(update_fields=["discord_username"])
    squad.captains.add(cap)
    client.force_login(event_admin)

    assert "shadowrider" in _profile_section(
        client.get(reverse("events:squad_manage", args=[squad.event_id])).content.decode()
    )
