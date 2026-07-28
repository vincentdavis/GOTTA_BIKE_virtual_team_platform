"""Registration guards for the zauth migration's Constance settings.

A setting missing from CONSTANCE_CONFIG_FIELDSETS still works in code but never
appears at /site/config/, so both the default and the fieldset placement matter.
"""

import pytest
from django.conf import settings

ZAUTH_SETTINGS = ["ZAUTH_BANNER_ENABLED", "ZAUTH_BANNER_MESSAGE", "ZAUTH_VERIFICATION_REQUIRED"]


@pytest.mark.parametrize("key", ZAUTH_SETTINGS)
def test_setting_is_registered(key):
    assert key in settings.CONSTANCE_CONFIG


@pytest.mark.parametrize("key", ZAUTH_SETTINGS)
def test_setting_is_reachable_in_the_admin_ui(key):
    """Anything absent from every fieldset is invisible at /site/config/."""
    in_a_fieldset = any(key in keys for keys in settings.CONSTANCE_CONFIG_FIELDSETS.values())
    assert in_a_fieldset, f"{key} is not in any CONSTANCE_CONFIG_FIELDSETS group"


@pytest.mark.parametrize("key", ["ZAUTH_BANNER_ENABLED", "ZAUTH_VERIFICATION_REQUIRED"])
def test_toggles_default_off(key):
    """Both roll out by an explicit admin action, never by deploying."""
    default, _description, field_type = settings.CONSTANCE_CONFIG[key]
    assert default is False
    assert field_type is bool


def test_the_cutover_flag_is_grouped_with_the_banner_settings():
    group = next(keys for keys in settings.CONSTANCE_CONFIG_FIELDSETS.values() if "ZAUTH_VERIFICATION_REQUIRED" in keys)
    assert "ZAUTH_BANNER_ENABLED" in group


def test_the_cutover_flag_advertises_that_it_is_inert():
    """It is registered ahead of the gating, so the description has to say so."""
    _default, description, _field_type = settings.CONSTANCE_CONFIG["ZAUTH_VERIFICATION_REQUIRED"]
    assert "NOT YET ENFORCED" in description
