"""Squad membership bounds on zFTP / zMAP.

Both units are supported because the ZRL division tables combine them -- "zFTP <
3.74 W/kg AND >= 200W" cannot be expressed in W/kg alone. Unlike the category
rules, a rider with no data is *blocked* by an enforced bound: these metrics only
exist for zauth-connected riders, so "no data" means "not connected".
"""

from decimal import Decimal

import pytest

from apps.events.models import Squad


def _squad(**kwargs) -> Squad:
    """Build an unsaved squad with the given bounds.

    Returns:
        The squad.

    """
    return Squad(name="Test", **kwargs)


@pytest.mark.parametrize(
    ("wkg", "expected"),
    [(3.00, True), (3.74, True), (3.75, False)],
)
def test_max_wkg_bound(wkg, expected) -> None:
    squad = _squad(max_zftp_wkg=Decimal("3.74"), enforce_max_zftp_wkg=True)
    assert squad.check_zftp_eligibility(250.0, wkg)[0] is expected


@pytest.mark.parametrize(
    ("watts", "expected"),
    [(199.0, False), (200.0, True), (260.0, True)],
)
def test_min_watt_bound(watts, expected) -> None:
    squad = _squad(min_zftp_w=Decimal("200.0"), enforce_min_zftp_w=True)
    assert squad.check_zftp_eligibility(watts, 3.0)[0] is expected


def test_the_real_zrl_rule_needs_both_units() -> None:
    """B DEV is 'zFTP < 3.74 W/kg AND >= 200W' -- each unit rejects a different rider."""
    squad = _squad(
        max_zftp_wkg=Decimal("3.74"), enforce_max_zftp_wkg=True,
        min_zftp_w=Decimal("200.0"), enforce_min_zftp_w=True,
    )
    assert squad.check_zftp_eligibility(240.0, 3.5)[0] is True      # inside both
    assert squad.check_zftp_eligibility(300.0, 4.2)[0] is False     # too strong on W/kg
    assert squad.check_zftp_eligibility(180.0, 3.0)[0] is False     # under the watt floor


def test_missing_data_is_blocked_with_a_useful_reason() -> None:
    squad = _squad(max_zftp_wkg=Decimal("3.74"), enforce_max_zftp_wkg=True)
    ok, reason = squad.check_zftp_eligibility(None, None)
    assert ok is False
    assert "connect Zwift" in reason


def test_missing_data_passes_when_nothing_is_enforced() -> None:
    """A squad with no power bounds must not start rejecting unconnected riders."""
    assert _squad().check_zftp_eligibility(None, None) == (True, "")
    assert _squad(max_zftp_wkg=Decimal("3.74")).check_zftp_eligibility(None, None) == (True, "")


def test_a_bound_set_but_not_enforced_does_nothing() -> None:
    squad = _squad(max_zftp_wkg=Decimal("1.00"))
    assert squad.check_zftp_eligibility(400.0, 6.0)[0] is True


def test_zmap_is_wired_to_its_own_fields() -> None:
    """A zFTP bound must not leak into the zMAP check, or vice versa."""
    squad = _squad(max_zmap_wkg=Decimal("4.53"), enforce_max_zmap_wkg=True)
    assert squad.check_zmap_eligibility(340.0, 5.0)[0] is False
    assert squad.check_zftp_eligibility(340.0, 5.0)[0] is True


def test_enforcement_summary_lists_the_power_bounds() -> None:
    squad = _squad(
        max_zftp_wkg=Decimal("3.74"), enforce_max_zftp_wkg=True,
        min_zftp_w=Decimal("200.0"), enforce_min_zftp_w=True,
    )
    summary = squad.enforcement_summary
    assert "zFTP: <= 3.74 W/kg" in summary
    assert "zFTP: >= 200.0 W" in summary
