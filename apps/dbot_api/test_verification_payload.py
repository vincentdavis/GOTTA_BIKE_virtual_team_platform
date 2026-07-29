"""The verification fields the Discord bot receives.

Once ZAUTH_VERIFICATION_REQUIRED is on, the bot has to tell the same story as the
roster and the profile banner — but race readiness must not move, since it never
consulted zwid_verified in the first place.
"""

import pytest
from constance.test import override_config

API_KEY = "test-bot-key"
GUILD_ID = 123456789012345678  # constance types this as int; the header is its string form
DISCORD_ID = "987654321098765432"


@pytest.fixture
def bot_headers():
    return {
        "HTTP_X_API_KEY": API_KEY,
        "HTTP_X_GUILD_ID": str(GUILD_ID),
        "HTTP_X_DISCORD_USER_ID": DISCORD_ID,
    }


@pytest.fixture
def legacy_member(db, user_model, zp_team_rider_factory):
    # Both endpoints 404 without ZwiftPower/ZwiftRacing data for the zwid.
    zp_team_rider_factory(zwid=4242)
    return user_model.objects.create_user(
        username="legacy-rider",
        discord_id=DISCORD_ID,
        discord_username="legacyrider",
        zwid=4242,
        zwid_verified=True,
        zwid_verification_method="legacy",
        is_race_ready=True,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("required", "expected_verified"),
    [(False, True), (True, False)],
)
def test_my_profile_reports_verification_under_the_active_policy(
    client, bot_headers, legacy_member, required, expected_verified
):
    with override_config(DBOT_AUTH_KEY=API_KEY, GUILD_ID=GUILD_ID, ZAUTH_VERIFICATION_REQUIRED=required):
        resp = client.get("/api/dbot/my_profile", **bot_headers)

    assert resp.status_code == 200
    assert resp.json()["zwid_verified"] is expected_verified


@pytest.mark.django_db
def test_race_ready_stays_raw_when_verification_is_required(client, bot_headers, legacy_member):
    """The cutover must never move anyone's race-ready Discord role."""
    with override_config(DBOT_AUTH_KEY=API_KEY, GUILD_ID=GUILD_ID, ZAUTH_VERIFICATION_REQUIRED=True):
        resp = client.get("/api/dbot/my_profile", **bot_headers)

    body = resp.json()
    assert body["zwid_verified"] is False  # policy applied
    assert body["is_race_ready"] is True  # but race readiness untouched


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("required", "expected_verified"),
    [(False, True), (True, False)],
)
def test_teammate_profile_uses_the_same_policy(client, bot_headers, legacy_member, required, expected_verified):
    """A teammate lookup must not disagree with the requester's own profile."""
    with override_config(DBOT_AUTH_KEY=API_KEY, GUILD_ID=GUILD_ID, ZAUTH_VERIFICATION_REQUIRED=required):
        resp = client.get(f"/api/dbot/teammate_profile/{legacy_member.zwid}", **bot_headers)

    assert resp.status_code == 200
    assert resp.json()["account"]["zwid_verified"] is expected_verified
