"""Smoke tests for ZwiftPower views."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.zwiftpower.models import ZPEvent, ZPRiderResults


@pytest.fixture
def zp_event(db) -> ZPEvent:
    return ZPEvent.objects.create(
        zid=998877,
        title="Friday Night Crit",
        event_date=timezone.now() - timedelta(days=2),
    )


@pytest.fixture
def zp_result(db, zp_event) -> ZPRiderResults:
    return ZPRiderResults.objects.create(
        event=zp_event,
        zid=zp_event.zid,
        zwid=12345,
        name="Test Rider",
        pos=1,
        position_in_cat=1,
        category="B",
        avg_power=250,
        avg_wkg="3.5",
        w1200=280,
        ftp=270,
        weight="71.5",
    )


@pytest.mark.django_db
def test_team_results_renders_with_data(auth_client, zp_result) -> None:
    response = auth_client.get(reverse("zwiftpower:team_results"))
    assert response.status_code == 200
    assert b"Test Rider" in response.content
    assert b"Friday Night Crit" in response.content


@pytest.mark.django_db
def test_team_results_search_filters(auth_client, zp_result) -> None:
    response = auth_client.get(reverse("zwiftpower:team_results"), {"q": "Nobody"})
    assert response.status_code == 200
    assert b"Test Rider" not in response.content
    assert b"No results match" in response.content


@pytest.mark.django_db
def test_team_results_sort_by_avg_power(auth_client, zp_result) -> None:
    response = auth_client.get(
        reverse("zwiftpower:team_results"),
        {"sort": "avg_power", "dir": "desc"},
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_team_results_unknown_sort_falls_back(auth_client, zp_result) -> None:
    response = auth_client.get(
        reverse("zwiftpower:team_results"),
        {"sort": "evil; DROP TABLE", "dir": "haha"},
    )
    assert response.status_code == 200
    assert b"Test Rider" in response.content


@pytest.mark.django_db
def test_event_results_renders(auth_client, zp_result, zp_event) -> None:
    response = auth_client.get(reverse("zwiftpower:event_results", args=[zp_event.zid]))
    assert response.status_code == 200
    assert b"Friday Night Crit" in response.content
    assert b"Test Rider" in response.content


@pytest.mark.django_db
def test_event_results_404_when_unknown(auth_client) -> None:
    response = auth_client.get(reverse("zwiftpower:event_results", args=[999999]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_team_results_requires_team_member(client, user) -> None:
    """A logged-in user without team_member permission cannot view team results."""
    client.force_login(user)
    response = client.get(reverse("zwiftpower:team_results"))
    # team_member_required redirects (302) or 403s — both are valid blocks
    assert response.status_code in {302, 403}


@pytest.mark.django_db
def test_results_link_to_user_when_zwid_matches(auth_client, team_member, zp_result) -> None:
    """A result whose zwid matches a User renders the tooltip partial with a profile link."""
    team_member.zwid = zp_result.zwid
    team_member.first_name = "Linked"
    team_member.last_name = "Rider"
    team_member.save(update_fields=["zwid", "first_name", "last_name"])

    response = auth_client.get(reverse("zwiftpower:team_results"))
    assert response.status_code == 200
    # tooltip partial uses the public_profile URL and Linked Rider display name
    assert b"Linked Rider" in response.content
    assert f"/user/profile/{team_member.pk}/".encode() in response.content


@pytest.mark.django_db
def test_results_show_plain_name_when_unlinked(auth_client, zp_result) -> None:
    """A result whose zwid does not match any User falls back to the plain ZP name."""
    response = auth_client.get(reverse("zwiftpower:team_results"))
    assert response.status_code == 200
    assert b"Test Rider" in response.content
    # The tooltip partial wraps the name in a hover dropdown. With no linked user,
    # the dropdown wrapper should not appear around the result name cell.
    assert b"dropdown-hover" not in response.content
