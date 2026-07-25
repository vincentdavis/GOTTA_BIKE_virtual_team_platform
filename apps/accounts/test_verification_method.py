"""Tests for the Zwift verification-method fields and the grandfather migration.

The suite runs with ``--no-migrations`` (schema built from model state), so the
data migration is not replayed automatically; its function is exercised directly
against the live model instead.
"""

import importlib

import pytest
from django.apps import apps as django_apps

_MIGRATION = importlib.import_module(
    "apps.accounts.migrations.0018_user_zwid_verification_method_user_zwid_verified_at"
)


@pytest.mark.django_db
def test_new_user_has_empty_verification_method(user):
    assert user.zwid_verification_method == ""
    assert user.zwid_verified_at is None


@pytest.mark.django_db
def test_verification_method_choices():
    from apps.accounts.models import User

    values = {c[0] for c in User.VerificationMethod.choices}
    assert values == {"legacy", "zauth", "admin"}


@pytest.mark.django_db
def test_grandfather_marks_verified_users_legacy(user_model):
    verified = user_model.objects.create_user(username="v", zwid=111, zwid_verified=True)
    unverified = user_model.objects.create_user(username="u", zwid=222, zwid_verified=False)
    already = user_model.objects.create_user(
        username="z", zwid=333, zwid_verified=True, zwid_verification_method="zauth"
    )

    _MIGRATION.grandfather_legacy_verifications(django_apps, None)

    verified.refresh_from_db()
    unverified.refresh_from_db()
    already.refresh_from_db()
    assert verified.zwid_verification_method == "legacy"
    assert unverified.zwid_verification_method == ""  # not verified -> untouched
    assert already.zwid_verification_method == "zauth"  # existing method preserved (idempotent)
