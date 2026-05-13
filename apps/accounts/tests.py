"""Smoke tests verifying pytest+Django wiring and core fixtures."""

import pytest


@pytest.mark.django_db
def test_user_fixture_creates_persisted_user(user) -> None:
    assert user.pk is not None
    assert user.username == "plain_user"
    assert user.is_superuser is False


@pytest.mark.django_db
def test_team_member_has_team_member_permission(team_member) -> None:
    assert team_member.has_permission("team_member") is True
    assert team_member.has_permission("app_admin") is False


@pytest.mark.django_db
def test_app_admin_has_app_admin_permission(app_admin) -> None:
    assert app_admin.has_permission("app_admin") is True
    assert app_admin.is_app_admin is True


@pytest.mark.django_db
def test_superuser_bypasses_all_permission_checks(superuser) -> None:
    assert superuser.has_permission("app_admin") is True
    assert superuser.has_permission("team_captain") is True
    assert superuser.has_permission("racing_admin") is True


@pytest.mark.django_db
def test_auth_client_logged_in_as_team_member(auth_client, team_member) -> None:
    assert auth_client.session.get("_auth_user_id") == str(team_member.pk)
