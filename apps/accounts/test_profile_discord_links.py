"""The public profile's Discord name and buttons open the rider's Discord profile.

Discord has no link that opens a DM from a user id, so both go to the profile, whose own
Message button starts one: the name and "Discord profile" in the browser, "Message on
Discord" in the Discord app (``discord://``).
"""

import re

import pytest
from django.urls import reverse

DISCORD_ID = "123456789012345678"
WEB_PROFILE = f"https://discord.com/users/{DISCORD_ID}"
APP_PROFILE = f"discord://-/users/{DISCORD_ID}"


def _body(client, viewer, target):
    """Render a public profile.

    Returns:
        The response body.

    """
    client.force_login(viewer)
    return client.get(reverse("accounts:public_profile", args=[target.pk])).content.decode()


def _links(body, href):
    """Find the links to one address.

    Returns:
        Each matching ``<a>`` element's markup, in page order.

    """
    return re.findall(rf'<a href="{re.escape(href)}".*?</a>', body, re.S)


def _rider(user_model, **extra):
    return user_model.objects.create_user(
        username="discord-rider",
        email="discord-rider@example.test",
        first_name="Sam",
        last_name="Lane",
        permission_overrides={"team_member": True},
        **extra,
    )


@pytest.mark.django_db
def test_the_name_and_both_buttons_open_their_discord_profile(client, team_member, user_model):
    rider = _rider(user_model, discord_id=DISCORD_ID, discord_username="samlane", discord_nickname="Sam L")
    body = _body(client, team_member, rider)

    name, button = _links(body, WEB_PROFILE)
    assert "Sam L" in name
    assert "Discord profile" in button
    for link in (name, button):
        assert 'target="_blank"' in link
        assert 'rel="noopener"' in link

    (message,) = _links(body, APP_PROFILE)
    assert "Message on Discord" in message


@pytest.mark.django_db
def test_without_a_nickname_the_username_is_the_link(client, team_member, user_model):
    rider = _rider(user_model, discord_id=DISCORD_ID, discord_username="samlane")

    name, _button = _links(_body(client, team_member, rider), WEB_PROFILE)

    assert "@samlane" in name


@pytest.mark.django_db
def test_without_a_discord_id_the_name_is_plain_text_and_there_are_no_buttons(client, team_member, user_model):
    rider = _rider(user_model, discord_username="samlane")
    body = _body(client, team_member, rider)

    assert "@samlane" in body
    assert "discord.com/users/" not in body
    assert "discord://" not in body
    assert "Message on Discord" not in body
