"""The Django admin cannot repoint a Discord social account.

The registration Zwift carry-over trusts ``SocialAccount`` to say which Discord login an
account holds, because ``User.discord_id`` is editable in the admin. allauth's own admin left
``user`` and ``uid`` editable, so a staff user could set both to a registrant's and collect the
registrant's Zwift link. Deleting a row stays possible: that is how staff let a rider's next
Discord login reconnect to their account by ``discord_id``.
"""

import pytest
from allauth.socialaccount.models import SocialAccount
from django.contrib import admin
from django.urls import reverse

from apps.accounts.admin import SocialAccountAdmin

REGISTRANT_DISCORD_ID = "800000000000000001"


@pytest.fixture
def staff_account(superuser):
    """Give the superuser fixture a Discord login of its own.

    Returns:
        The social account.

    """
    return SocialAccount.objects.create(
        user=superuser, provider="discord", uid="800000000000000999", extra_data={"id": "800000000000000999"}
    )


def test_the_project_admin_replaces_allauths():
    assert isinstance(admin.site._registry[SocialAccount], SocialAccountAdmin)


@pytest.mark.django_db
def test_the_identity_fields_are_read_only(rf, superuser, staff_account):
    model_admin = admin.site._registry[SocialAccount]
    request = rf.get("/")
    request.user = superuser

    readonly = model_admin.get_readonly_fields(request, staff_account)

    assert {"user", "provider", "uid"} <= set(readonly)
    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_delete_permission(request, staff_account) is True


@pytest.mark.django_db
def test_a_change_post_cannot_move_the_uid_or_the_user(client, superuser, user_model, staff_account):
    registrant = user_model.objects.create_user(username="registrant", discord_id=REGISTRANT_DISCORD_ID)
    client.force_login(superuser)
    url = reverse("admin:socialaccount_socialaccount_change", args=[staff_account.pk])

    assert client.get(url).status_code == 200
    client.post(
        url,
        {"user": registrant.pk, "provider": "discord", "uid": REGISTRANT_DISCORD_ID, "extra_data": "{}"},
    )

    staff_account.refresh_from_db()
    assert staff_account.uid == "800000000000000999"
    assert staff_account.user_id == superuser.pk


@pytest.mark.django_db
def test_adding_one_by_hand_is_refused(client, superuser):
    client.force_login(superuser)

    response = client.post(
        reverse("admin:socialaccount_socialaccount_add"),
        {"user": superuser.pk, "provider": "discord", "uid": REGISTRANT_DISCORD_ID, "extra_data": "{}"},
    )

    assert response.status_code == 403
    assert not SocialAccount.objects.filter(uid=REGISTRANT_DISCORD_ID).exists()


@pytest.mark.django_db
def test_deleting_one_still_works(client, superuser, staff_account):
    client.force_login(superuser)

    client.post(reverse("admin:socialaccount_socialaccount_delete", args=[staff_account.pk]), {"post": "yes"})

    assert not SocialAccount.objects.filter(pk=staff_account.pk).exists()
