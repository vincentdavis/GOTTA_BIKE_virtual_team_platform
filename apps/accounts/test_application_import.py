"""Importing an approved registration onto the member's profile.

The registration's Zwift ID is no longer copied: a registration verifies Zwift through its
own zauth link (keyed by its UUID), and the import asks the service to move that link to the
member, then verifies the member from the service's answer. Every service call is patched at
the ``apps.zwift.client`` boundary.
"""

from unittest.mock import patch

import pytest
from allauth.socialaccount.models import SocialAccount
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.urls import reverse

from apps.accounts.services import (
    ZWIFT_LINK_KEY,
    can_carry_over_zwift,
    carry_over_zwift_link,
    get_importable_fields,
    import_application_to_user,
)
from apps.team.models import MembershipApplication
from apps.zwift.client import RelinkOutcome, RelinkResult
from gotta_bike_platform.config import settings as config

DISCORD_ID = "123456789012345678"


@pytest.fixture(autouse=True)
def _clear_cache():
    """Start each test without a remembered "no link" answer from another test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def member(user_model):
    """Build a member who signed in with Discord, with a blank profile.

    Returns:
        The member.

    """
    user = user_model.objects.create_user(username="newcomer", discord_id=DISCORD_ID, first_name="", last_name="")
    SocialAccount.objects.create(user=user, provider="discord", uid=DISCORD_ID)
    return user


@pytest.fixture
def application(db):
    """Build an approved registration that connected Zwift.

    Returns:
        The registration.

    """
    return MembershipApplication.objects.create(
        discord_id=DISCORD_ID,
        discord_username="newcomer",
        status=MembershipApplication.Status.APPROVED,
        first_name="Nova",
        last_name="Rider",
        timezone="Europe/London",
        zwift_id="4242",
        zwift_verified=True,
    )


@pytest.fixture
def service(monkeypatch, application):
    """Stand in for the zauth service, recording what was asked of it.

    Returns:
        A dict: set ``relink``, ``registration_status`` (the registration's link) and
        ``member_status`` (the member's, after a move) to shape the answers; read ``calls``
        for what was requested.

    """
    state = {
        "relink": RelinkResult(RelinkOutcome.MOVED, "4242"),
        "registration_status": {"connected": True, "zwid": "4242", "connected_at": None},
        "member_status": {"connected": True, "zwid": "4242", "connected_at": None},
        "calls": [],
    }

    def relink(from_id, to_id):
        state["calls"].append(("relink", from_id, to_id))
        return state["relink"]

    def status(user_id):
        state["calls"].append(("status", user_id))
        return state["registration_status"] if user_id == str(application.pk) else state["member_status"]

    def disconnect(user_id):
        state["calls"].append(("disconnect", user_id))
        return True

    monkeypatch.setattr("apps.zwift.client.relink_connection", relink)
    monkeypatch.setattr("apps.zwift.client.get_connection_status", status)
    monkeypatch.setattr("apps.zwift.client.disconnect", disconnect)
    monkeypatch.setattr("apps.zwift.client.is_configured", lambda: True)
    monkeypatch.setattr("apps.zwift.client.get_racing_profile", lambda user_id: None)
    return state


def _import(client, member, application):
    client.force_login(member)
    return client.post(reverse("accounts:import_application", args=[application.pk]))


def _messages(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


def _calls(service, kind, user_id=None):
    return [c for c in service["calls"] if c[0] == kind and (user_id is None or c[1] == user_id)]


# --- what is offered ----------------------------------------------------------------------


@pytest.mark.django_db
def test_the_zwift_id_is_no_longer_a_copied_field(member, application, service):
    fields = get_importable_fields(application, member)

    assert "zwift_id" not in fields
    assert "zwift_verified" not in fields
    assert fields[ZWIFT_LINK_KEY]["user_field"] == ""
    assert "4242" in fields[ZWIFT_LINK_KEY]["display_value"]


@pytest.mark.django_db
def test_the_row_shows_the_zwid_the_service_reports(member, application, service):
    service["registration_status"] = {"connected": True, "zwid": "6161"}

    fields = get_importable_fields(application, member)

    assert fields[ZWIFT_LINK_KEY]["display_value"] == "Connected, Zwift ID 6161"


@pytest.mark.django_db
def test_fields_the_member_already_filled_are_not_offered(member, application, service):
    member.first_name = "Already"
    member.save(update_fields=["first_name"])

    fields = get_importable_fields(application, member)

    assert "first_name" not in fields
    assert "last_name" in fields


@pytest.mark.django_db
def test_a_no_the_member_already_gave_is_not_offered_again(client, member, application):
    """``dual_recording`` is nullable, so False is an answer, and the banner has to go."""
    application.zwift_verified = False
    application.dual_recording = False
    application.save(update_fields=["zwift_verified", "dual_recording"])
    member.first_name, member.last_name, member.timezone = "Nova", "Rider", "Europe/London"
    member.dual_recording = False
    member.save()

    assert get_importable_fields(application, member) == {}
    assert import_application_to_user(member, application) == []
    client.force_login(member)
    body = client.get(reverse("accounts:profile_edit")).content.decode()
    assert reverse("accounts:import_application", args=[application.pk]) not in body


@pytest.mark.django_db
def test_an_unanswered_yes_or_no_is_still_imported(member, application):
    application.zwift_verified = False
    application.dual_recording = False
    application.save(update_fields=["zwift_verified", "dual_recording"])

    assert "dual_recording" in get_importable_fields(application, member)
    assert "Dual Recording" in import_application_to_user(member, application)

    member.refresh_from_db()
    assert member.dual_recording is False
    assert "dual_recording" not in get_importable_fields(application, member)


@pytest.mark.django_db
def test_no_carry_over_is_offered_to_a_zauth_verified_member(member, application, service):
    member.zwid = 999
    member.zwid_verified = True
    member.zwid_verification_method = "zauth"
    member.save()

    assert can_carry_over_zwift(member, application) is False
    assert ZWIFT_LINK_KEY not in get_importable_fields(application, member)
    assert service["calls"] == []


@pytest.mark.django_db
def test_a_legacy_verified_member_is_still_offered_it(member, application, service):
    """Legacy verifications are what the migration replaces, so they do not block the move."""
    member.zwid = 999
    member.zwid_verified = True
    member.zwid_verification_method = "legacy"
    member.save()

    assert can_carry_over_zwift(member, application) is True


@pytest.mark.django_db
def test_no_carry_over_is_offered_for_an_unverified_registration(member, application, service):
    application.zwift_verified = False
    application.save(update_fields=["zwift_verified"])

    assert ZWIFT_LINK_KEY not in get_importable_fields(application, member)
    assert service["calls"] == []


@pytest.mark.django_db
def test_a_registration_verified_by_a_retired_path_is_not_offered(client, member, application, service):
    """The Sauce flow and the staff grant set ``zwift_verified`` with no link behind it."""
    service["registration_status"] = {"connected": False}
    member.first_name, member.last_name, member.timezone = "Nova", "Rider", "Europe/London"
    member.save()

    assert can_carry_over_zwift(member, application) is False
    client.force_login(member)
    body = client.get(reverse("accounts:profile_edit")).content.decode()
    assert reverse("accounts:import_application", args=[application.pk]) not in body

    # The "no" is remembered: the profile page does not ask on every load.
    asked = len(_calls(service, "status", str(application.pk)))
    get_importable_fields(application, member)
    assert len(_calls(service, "status", str(application.pk))) == asked


@pytest.mark.django_db
def test_nothing_is_offered_while_the_service_cannot_say(member, application, service):
    service["registration_status"] = None

    assert can_carry_over_zwift(member, application) is False
    # Not remembered: an outage is not an answer.
    service["registration_status"] = {"connected": True, "zwid": "4242"}
    assert can_carry_over_zwift(member, application) is True


@pytest.mark.django_db
def test_nothing_is_offered_when_zauth_is_not_configured(member, application, monkeypatch):
    monkeypatch.setattr(config, "zwift_api_base_url", None)
    monkeypatch.setattr(config, "zwift_app_api_key", None)

    assert ZWIFT_LINK_KEY not in get_importable_fields(application, member)
    assert carry_over_zwift_link(member, application).outcome == "skipped"


@pytest.mark.django_db
def test_the_carry_over_needs_the_registrants_own_discord_login(client, user_model, application, service):
    """``User.discord_id`` is editable in the admin, so it cannot be what hands over a verification.

    Pointing a staff account at a registrant's Discord ID must not collect the registrant's
    Zwift account.
    """
    staff = user_model.objects.create_user(username="staff", discord_id=DISCORD_ID, is_staff=True)
    SocialAccount.objects.create(user=staff, provider="discord", uid="555555555555555555")

    assert can_carry_over_zwift(staff, application) is False
    _import(client, staff, application)

    staff.refresh_from_db()
    assert staff.zwid_verified is False
    assert _calls(service, "relink") == []


@pytest.mark.django_db
def test_an_account_with_no_discord_login_is_not_offered_it(user_model, application, service):
    bare = user_model.objects.create_user(username="bare", discord_id=DISCORD_ID)

    assert can_carry_over_zwift(bare, application) is False


@pytest.mark.django_db
def test_a_blank_discord_id_matches_nothing(client, user_model, application, service):
    """An account without Discord must not import a registration that has none either."""
    application.discord_id = ""
    application.save(update_fields=["discord_id"])
    no_discord = user_model.objects.create_user(username="nodiscord", discord_id="")

    response = _import(client, no_discord, application)

    assert response.status_code == 403
    no_discord.refresh_from_db()
    assert no_discord.first_name == ""
    assert service["calls"] == []


@pytest.mark.django_db
def test_the_confirmation_page_explains_the_move(client, member, application, service):
    client.force_login(member)

    body = client.get(reverse("accounts:import_application", args=[application.pk])).content.decode()

    assert "Zwift Account" in body
    assert "moves that connection to your" in body
    # The move replaces a Zwift ID already on the profile, so the page must not promise otherwise.
    assert "replaces any Zwift ID" in body
    assert "Existing data will not be changed" not in body


@pytest.mark.django_db
def test_the_confirmation_page_says_nothing_about_zwift_without_a_link(client, member, application, service):
    service["registration_status"] = {"connected": False}
    client.force_login(member)

    body = client.get(reverse("accounts:import_application", args=[application.pk])).content.decode()

    assert "Zwift Account" not in body
    assert "connected to Zwift" not in body


# --- the banner ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_banner_offers_the_zwift_move(client, member, application, service):
    client.force_login(member)

    body = client.get(reverse("accounts:profile_edit")).content.decode()

    assert reverse("accounts:import_application", args=[application.pk]) in body
    assert "moves your registration's Zwift connection to your account" in body


@pytest.mark.django_db
def test_the_banner_goes_away_once_there_is_nothing_left_to_import(client, member, application, service):
    """It used to list every non-empty registration field, so it never went away."""
    _import(client, member, application)
    member.refresh_from_db()
    assert member.is_zauth_verified

    body = client.get(reverse("accounts:profile_edit")).content.decode()

    assert reverse("accounts:import_application", args=[application.pk]) not in body


# --- the move -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_moved_link_verifies_the_member_with_the_service_zwid(client, member, application, service):
    service["member_status"] = {"connected": True, "zwid": "5151", "connected_at": None}

    response = _import(client, member, application)

    member.refresh_from_db()
    relink_at = service["calls"].index(("relink", str(application.pk), str(member.pk)))
    assert service["calls"][relink_at + 1] == ("status", str(member.pk))
    assert member.zwid == 5151  # the service's zwid, not the one typed on the registration
    assert member.zwid_verified is True
    assert member.zwid_verification_method == "zauth"
    assert member.zwid_verified_at is not None
    assert member.first_name == "Nova"  # ordinary fields still import
    assert any("Zwift ID 5151" in m for m in _messages(response))


@pytest.mark.django_db
def test_a_legacy_zwid_is_replaced_by_the_one_zwift_reports(client, member, application, service):
    member.zwid = 999
    member.zwid_verified = True
    member.zwid_verification_method = "legacy"
    member.save()

    _import(client, member, application)

    member.refresh_from_db()
    assert member.zwid == 4242
    assert member.is_zauth_verified


@pytest.mark.django_db
def test_the_relink_answer_stands_in_when_the_status_read_fails(member, application, service):
    service["member_status"] = None
    service["relink"] = RelinkResult(RelinkOutcome.MOVED, "4242")

    result = carry_over_zwift_link(member, application)

    member.refresh_from_db()
    assert result.outcome == "moved"
    assert result.verified is True
    assert member.zwid == 4242
    assert member.is_zauth_verified


@pytest.mark.django_db
def test_a_move_the_service_cannot_confirm_leaves_the_member_unverified(client, member, application, service):
    service["member_status"] = None
    service["relink"] = RelinkResult(RelinkOutcome.MOVED, None)

    response = _import(client, member, application)

    member.refresh_from_db()
    assert member.zwid_verified is False
    assert any("once the Zwift service confirms it" in m for m in _messages(response))


@pytest.mark.django_db
def test_on_a_conflict_the_members_own_link_wins(client, member, application, service):
    service["relink"] = RelinkResult(RelinkOutcome.CONFLICT)
    service["member_status"] = {"connected": True, "zwid": "7777", "connected_at": None}

    response = _import(client, member, application)

    member.refresh_from_db()
    assert ("disconnect", str(application.pk)) in service["calls"]
    assert member.zwid == 7777  # their own link, applied
    assert member.is_zauth_verified
    assert any("was not used" in m for m in _messages(response))


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (RelinkOutcome.NOT_FOUND, "couldn't find the Zwift connection on your registration"),
        (RelinkOutcome.ERROR, "connect Zwift from your profile"),
        (RelinkOutcome.UNCONFIGURED, "connect Zwift from your profile"),
    ],
)
def test_a_failed_move_changes_nothing_and_says_what_to_do(client, member, application, service, outcome, expected):
    service["relink"] = RelinkResult(outcome)
    # The member's own status, read back after the failure, confirms nothing moved.
    service["member_status"] = {"connected": False, "zwid": None, "connected_at": None}

    response = _import(client, member, application)

    member.refresh_from_db()
    assert member.zwid is None  # the typed registration zwid is never copied across
    assert member.zwid_verified is False
    assert ("disconnect", str(application.pk)) not in service["calls"]
    assert any(expected in m for m in _messages(response))
    assert not any("is now linked to your profile" in m for m in _messages(response))


@pytest.mark.django_db
def test_nothing_is_asked_for_a_zauth_verified_member(member, application, service):
    member.zwid = 999
    member.zwid_verified = True
    member.zwid_verification_method = "zauth"
    member.save()

    result = carry_over_zwift_link(member, application)

    assert result.outcome == "skipped"
    assert service["calls"] == []


@pytest.mark.django_db
def test_an_unverified_registration_copies_no_zwid(client, member, application, service):
    """Before, a typed-in registration zwid landed on the account; now it goes nowhere."""
    application.zwift_verified = False
    application.save(update_fields=["zwift_verified"])

    _import(client, member, application)

    member.refresh_from_db()
    assert member.zwid is None
    assert member.zwid_verified is False
    assert _calls(service, "relink") == []
    assert _calls(service, "status", str(application.pk)) == []


@pytest.mark.django_db
def test_the_carry_over_log_has_ids_only(member, application, service):
    with patch("apps.accounts.services.logfire") as fake_logfire:
        carry_over_zwift_link(member, application)

    kwargs = fake_logfire.info.call_args.kwargs
    assert kwargs["user_id"] == member.pk
    assert kwargs["application_id"] == str(application.pk)
    assert kwargs["outcome"] == "moved"
    assert not [name for name in kwargs if "auth" in name]
    assert "Nova" not in str(fake_logfire.mock_calls)


@pytest.mark.django_db
def test_someone_elses_registration_cannot_be_imported(client, user_model, application, service):
    stranger = user_model.objects.create_user(username="stranger", discord_id="999")
    client.force_login(stranger)

    response = client.post(reverse("accounts:import_application", args=[application.pk]))

    assert response.status_code == 403
    assert service["calls"] == []


# --- reading the member back after a failed move -------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "outcome",
    [RelinkOutcome.ERROR, RelinkOutcome.NOT_FOUND],
    ids=["response-lost-after-the-move", "second-submit-finds-it-moved"],
)
def test_a_move_that_happened_anyway_shows_success(client, member, application, service, outcome):
    """A lost 200 and a double submit both read as failures, yet the link is the member's."""
    service["relink"] = RelinkResult(outcome)
    service["member_status"] = {"connected": True, "zwid": "4242", "connected_at": None}

    response = _import(client, member, application)

    member.refresh_from_db()
    assert member.is_zauth_verified
    assert member.zwid == 4242
    shown = _messages(response)
    assert any("Zwift ID 4242" in m and "is now linked to your profile" in m for m in shown)
    assert not any("connect Zwift from your profile" in m for m in shown)
    # And the banner, which is keyed on the member's state, is gone with it.
    body = client.get(reverse("accounts:profile_edit")).content.decode()
    assert reverse("accounts:import_application", args=[application.pk]) not in body


@pytest.mark.django_db
def test_an_unanswered_read_after_an_error_takes_nothing_away(member, application, service):
    """``apply_status`` never revokes on None, so a legacy verification survives the read."""
    member.zwid = 999
    member.zwid_verified = True
    member.zwid_verification_method = "legacy"
    member.save()
    service["relink"] = RelinkResult(RelinkOutcome.ERROR)
    service["member_status"] = None

    result = carry_over_zwift_link(member, application)

    member.refresh_from_db()
    assert result.outcome == "error"
    assert result.verified is False
    assert (member.zwid, member.zwid_verified, member.zwid_verification_method) == (999, True, "legacy")
    assert ("status", str(member.pk)) in service["calls"]


@pytest.mark.django_db
def test_unconfigured_does_not_read_the_member_back(member, application, service):
    service["relink"] = RelinkResult(RelinkOutcome.UNCONFIGURED)

    carry_over_zwift_link(member, application)

    assert _calls(service, "status", str(member.pk)) == []


# --- asking the service once --------------------------------------------------------------


@pytest.mark.django_db
def test_the_import_post_asks_for_the_registration_link_once(client, member, application, service):
    _import(client, member, application)

    assert len(_calls(service, "status", str(application.pk))) == 1
    assert len(_calls(service, "relink")) == 1


@pytest.mark.django_db
def test_a_passed_offer_skips_only_the_service_read(user_model, application, service):
    """The checks that cost nothing are made again, so ``offered`` cannot hand the link to anyone."""
    other = user_model.objects.create_user(username="other", discord_id=DISCORD_ID)

    result = carry_over_zwift_link(other, application, offered=True)

    assert result.outcome == "skipped"
    assert service["calls"] == []


@pytest.mark.django_db
def test_a_passed_offer_is_trusted_for_the_link_itself(member, application, service):
    result = carry_over_zwift_link(member, application, offered=True)

    assert result.outcome == "moved"
    assert _calls(service, "status", str(application.pk)) == []


@pytest.mark.django_db
def test_a_refused_offer_asks_nothing(member, application, service):
    assert carry_over_zwift_link(member, application, offered=False).outcome == "skipped"
    assert service["calls"] == []


# --- staff ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_carry_over_for_a_staff_account_is_flagged(member, application, service):
    member.is_staff = True
    member.save(update_fields=["is_staff"])

    with patch("apps.accounts.services.logfire") as fake_logfire:
        carry_over_zwift_link(member, application)

    kwargs = fake_logfire.warning.call_args.kwargs
    assert kwargs == {"user_id": member.pk, "discord_id": DISCORD_ID, "application_id": str(application.pk)}


@pytest.mark.django_db
def test_an_ordinary_carry_over_is_not_flagged(member, application, service):
    with patch("apps.accounts.services.logfire") as fake_logfire:
        carry_over_zwift_link(member, application)

    fake_logfire.warning.assert_not_called()


# --- wording when the link is all that is left ------------------------------------------------


def _fill_everything_but_the_link(member, application):
    for key, row in get_importable_fields(application, member).items():
        if key != ZWIFT_LINK_KEY:
            setattr(member, row["user_field"], row["value"])
    member.save()
    assert list(get_importable_fields(application, member)) == [ZWIFT_LINK_KEY]


@pytest.mark.django_db
def test_the_banner_does_not_promise_to_fill_a_full_profile(client, member, application, service):
    _fill_everything_but_the_link(member, application)
    client.force_login(member)

    body = client.get(reverse("accounts:profile_edit")).content.decode()

    assert "Your approved registration is connected to Zwift." in body
    assert "Import it to move that connection to your account." in body
    assert "auto-fill your profile" not in body
    assert "It also moves" not in body


@pytest.mark.django_db
def test_the_banner_keeps_the_fill_wording_while_fields_are_left(client, member, application, service):
    client.force_login(member)

    body = client.get(reverse("accounts:profile_edit")).content.decode()

    assert "auto-fill your profile" in body
    assert "Import it to move that connection" not in body


@pytest.mark.django_db
def test_the_confirmation_page_words_a_move_only_import(client, member, application, service):
    _fill_everything_but_the_link(member, application)
    client.force_login(member)

    body = client.get(reverse("accounts:import_application", args=[application.pk])).content.decode()

    assert "Importing it moves that connection to your account" in body
    assert "The following data from your membership registration will be imported" not in body
    assert "Only empty profile fields will be filled" not in body
