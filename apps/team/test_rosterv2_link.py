"""The not-linked worklists: who is on them, who must not be, and what they may carry.

Three populations, one filter. Two of them name something a rider has not done, so the cost
of a wrong query is accusing somebody -- most sharply for a member who signed in ten minutes
ago and whose cached guild row has not caught up yet.
"""

from dataclasses import fields

import pytest
from django.urls import reverse

from apps.accounts.models import GuildMember, User
from apps.team.rosterv2 import GUILD_GAP_COLUMNS, LinkRow, build_link_rows

EXPECTED_LINK_ROW_FIELDS = (
    "name", "discord_id", "discord_handle", "avatar_url",
    "user_id", "zwid", "joined_at", "_search", "matched_as",
)

# A worklist needs a name and a way to reach someone. It never needs a body or a birthday.
FORBIDDEN = ("weight_kg", "height_cm", "birth_year", "email", "first_name", "last_name")


def test_the_row_declares_exactly_these_fields():
    """A new field on a worklist row costs a deliberate edit here."""
    assert tuple(f.name for f in fields(LinkRow)) == EXPECTED_LINK_ROW_FIELDS


@pytest.mark.parametrize("forbidden", FORBIDDEN)
def test_the_row_carries_nothing_personal(forbidden):
    """first_name and last_name reach the SEARCH haystack, never the row itself."""
    assert forbidden not in {f.name for f in fields(LinkRow)}
    assert forbidden not in GUILD_GAP_COLUMNS


def test_the_forbidden_names_are_real_model_fields():
    """Guards the guard: a typo would make every assertion above vacuously true."""
    user_fields = {f.name for f in User._meta.get_fields()}
    for name in ("birth_year", "email", "first_name", "last_name"):
        assert name in user_fields, f"{name} is no longer a User field; the tests above prove nothing"


def test_every_guild_column_exists():
    """A misspelled column in the allow-list raises at query time, not here."""
    assert set(GUILD_GAP_COLUMNS) <= {f.name for f in GuildMember._meta.get_fields()}


def test_a_row_cannot_be_given_a_field_it_does_not_declare():
    """Frozen and slotted, like every other class in this module."""
    row = LinkRow(name="Ada")

    with pytest.raises((TypeError, AttributeError)):
        row.weight_kg = 91.7


@pytest.mark.django_db
def test_the_search_haystack_never_renders(auth_client):
    """It holds real names, so it needs the underscore AND repr=False."""
    row = LinkRow(name="Shown", _search=(("lovelace", "Ada Lovelace"),))

    assert "Lovelace" not in repr([row])
    assert "Lovelace" not in repr(row)


@pytest.mark.django_db
def test_a_bot_is_not_somebody_to_chase():
    """Bots are in the guild and have no account by design."""
    GuildMember.objects.create(discord_id="8001", username="a-bot", user=None, is_bot=True)

    assert build_link_rows("no_account") == []


@pytest.mark.django_db
def test_somebody_who_left_is_not_somebody_to_chase():
    """A worklist of people to nudge must not include people who are gone."""
    GuildMember.objects.create(
        discord_id="8002", username="gone", user=None, is_bot=False, date_left="2026-01-01T00:00:00Z"
    )

    assert build_link_rows("no_account") == []


@pytest.mark.django_db
def test_a_member_who_just_signed_in_is_not_accused(user_model):
    """The worst failure this list has: the guild sync runs every six hours.

    Until it does, a member who signed in minutes ago still has ``user=None`` on their cached
    row, and a query without the discord_id exclusion tells the team they never signed in.
    """
    user_model.objects.create_user(username="just-joined", email="jj@example.test", discord_id="8003")
    GuildMember.objects.create(discord_id="8003", username="just-joined", user=None, is_bot=False)

    assert build_link_rows("no_account") == []


@pytest.mark.django_db
@pytest.mark.parametrize("zwid", [None, 0])
def test_both_spellings_of_no_zwid_are_found(user_model, zwid):
    """0 is the sentinel the roster index excludes; null is the other spelling."""
    rider = user_model.objects.create_user(username="nz", email="nz@example.test", discord_id="8004")
    rider.zwid = zwid
    rider.save(update_fields=["zwid"])
    GuildMember.objects.create(discord_id="8004", username="nz", user=rider, is_bot=False)

    assert [r.name for r in build_link_rows("no_zwid")] == ["nz"]


@pytest.mark.django_db
def test_somebody_with_no_discord_row_is_not_a_member(user_model):
    """Without the membership clause the first row is the bootstrap superuser."""
    user_model.objects.create_user(username="service-account", email="sa@example.test")

    assert build_link_rows("no_zwid") == []


@pytest.mark.django_db
def test_the_toggle_is_membership_admin_only(client, team_member, user_model):
    """The same population is already behind that permission at /team/discord-review/."""
    GuildMember.objects.create(discord_id="8005", username="stranger", user=None, is_bot=False)
    client.force_login(team_member)

    body = client.get(reverse("team:roster") + "?link=no_account").content.decode()

    assert 'id="f-link"' not in body
    assert "never signed in here" not in body
    # And the chip must not claim a filter the page declined to apply.
    assert "No account here" not in body


@pytest.mark.django_db
def test_a_membership_admin_is_offered_the_control(client, membership_admin):
    """It sits with the other filters, as one select rather than three buttons."""
    GuildMember.objects.create(discord_id="8006", username="stranger", user=None, is_bot=False)
    client.force_login(membership_admin)

    body = client.get(reverse("team:roster")).content.decode()

    assert 'id="f-link"' in body
    assert "No account here" in body
    assert "Members, no stats" in body


@pytest.mark.django_db
def test_the_panel_stays_reachable_while_a_population_is_showing(client, membership_admin):
    """The control lives in the panel, so hiding the panel would strand the reader in it."""
    GuildMember.objects.create(discord_id="8012", username="stranger", user=None, is_bot=False)
    client.force_login(membership_admin)

    body = client.get(reverse("team:roster") + "?link=no_account").content.decode()

    assert 'id="f-link"' in body


@pytest.mark.django_db
def test_a_stat_filter_is_dropped_rather_than_applied(client, membership_admin):
    """Rule made structural: a stat filter over people with no stats returns nothing."""
    GuildMember.objects.create(discord_id="8007", username="stranger", user=None, is_bot=False)
    client.force_login(membership_admin)

    body = client.get(reverse("team:roster") + "?link=no_account&wkg=4.5").content.decode()

    assert "stranger" in body
    assert "Min W/kg" not in body


@pytest.mark.django_db
def test_the_no_stats_copy_does_not_blame_the_rider(client, membership_admin, user_model):
    """That list is our profile cache being behind, not anything a rider failed to do."""
    rider = user_model.objects.create_user(
        username="statless", email="s@example.test", discord_id="8008", zwid=99001
    )
    GuildMember.objects.create(discord_id="8008", username="statless", user=rider, is_bot=False)
    client.force_login(membership_admin)

    body = client.get(reverse("team:roster") + "?link=no_stats").content.decode()

    assert "statless" in body
    assert "nothing on this list for a rider to fix" in body


@pytest.mark.django_db
def test_the_copy_agrees_with_a_count_of_one(client, membership_admin):
    """These lists reach one deliberately -- the last unsigned-in member is when it is read."""
    GuildMember.objects.create(discord_id="8009", username="only-one", user=None, is_bot=False)
    client.force_login(membership_admin)

    body = client.get(reverse("team:roster") + "?link=no_account").content.decode()

    assert "1 person is in the Discord" in body
    assert "has never signed in here" in body
    assert "1 people" not in body


@pytest.mark.django_db
def test_the_copy_agrees_with_a_count_of_several(client, membership_admin):
    """And the plural still reads correctly."""
    for n in range(3):
        GuildMember.objects.create(discord_id=f"801{n}", username=f"m{n}", user=None, is_bot=False)
    client.force_login(membership_admin)

    body = client.get(reverse("team:roster") + "?link=no_account").content.decode()

    assert "3 people are in the Discord" in body
    assert "have never signed in here" in body


@pytest.mark.django_db
def test_the_sort_control_is_not_offered_on_a_worklist(client, membership_admin):
    """Every sort keys off a stat, so it would be a select that visibly does nothing."""
    GuildMember.objects.create(discord_id="8013", username="stranger", user=None, is_bot=False)
    client.force_login(membership_admin)

    cards = client.get(reverse("team:roster")).content.decode()
    worklist = client.get(reverse("team:roster") + "?link=no_account").content.decode()

    assert 'id="f-sort"' in cards
    assert 'id="f-sort"' not in worklist
    # Order survives, because join date really does have two directions.
    assert "Oldest first" in worklist
