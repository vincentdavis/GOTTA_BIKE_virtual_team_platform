"""Reaching a rider's profile by Zwift id.

The zwid is the identifier that travels between systems -- it is the join key for ZwiftPower,
ZwiftRacing, zauth and the roster, and the thing quoted in a result row or a Discord message.
Our own user id is known only to this app, so a link built from data that came from anywhere
else could not previously reach the profile at all.

The route redirects rather than rendering, so the profile view stays the single place that
decides what a viewer may see, and each rider keeps one canonical URL.
"""

import pytest
from django.urls import reverse


def _url(zwid: int) -> str:
    """Build the by-zwid profile URL.

    Args:
        zwid: The rider's Zwift id.

    Returns:
        The URL.

    """
    return reverse("accounts:public_profile_by_zwid", args=[zwid])


@pytest.mark.django_db
def test_it_redirects_to_the_canonical_profile_url(client, user_model, team_member):
    rider = user_model.objects.create_user(
        username="rider", email="rider@example.test", zwid=6164399, zwid_verified=True,
    )
    client.force_login(team_member)

    response = client.get(_url(6164399))

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("accounts:public_profile", args=[rider.pk])


@pytest.mark.django_db
def test_an_unclaimed_zwid_is_a_404(client, team_member):
    """A zwid that is a real rider elsewhere but belongs to no member here."""
    client.force_login(team_member)

    assert client.get(_url(999999)).status_code == 404


@pytest.mark.django_db
def test_two_accounts_claiming_one_zwid_raises(client, user_model, team_member):
    """Not resolved quietly: a duplicate is a data problem, and picking a winner hides it.

    Resolving it -- preferring the verified claim, say -- would send a viewer to one of two
    people with no sign anything was wrong, and would keep working for as long as nobody
    noticed. The schema allows this (zwid is indexed, not unique); it should not occur.
    """
    user_model.objects.create_user(username="one", email="one@example.test", zwid=6164399)
    user_model.objects.create_user(username="two", email="two@example.test", zwid=6164399)
    client.force_login(team_member)

    with pytest.raises(user_model.MultipleObjectsReturned):
        client.get(_url(6164399))


@pytest.mark.django_db
def test_it_is_gated_like_the_profile_it_points_at(client, user_model, user):
    """Gated on the route itself, not only on the redirect target.

    An open redirect would answer "is this zwid one of your members?" to anyone signed in,
    which is exactly what the team_member gate on the profile page exists to withhold.
    """
    user_model.objects.create_user(username="rider", email="rider@example.test", zwid=6164399)
    client.force_login(user)  # signed in, but not a team member

    assert client.get(_url(6164399)).status_code == 403


@pytest.mark.django_db
def test_it_requires_a_login(client, user_model):
    user_model.objects.create_user(username="rider", email="rider@example.test", zwid=6164399)

    response = client.get(_url(6164399))

    assert response.status_code == 302
    assert "/accounts/login/" in response.headers["Location"]


@pytest.mark.django_db
def test_following_the_redirect_reaches_the_rendered_profile(client, user_model, team_member):
    """End to end: the point of the route is landing on a working page."""
    user_model.objects.create_user(
        username="rider", email="rider@example.test", first_name="Alex", last_name="Rivera",
        zwid=6164399, zwid_verified=True,
    )
    client.force_login(team_member)

    response = client.get(_url(6164399), follow=True)

    assert response.status_code == 200
    assert "Alex" in response.content.decode()
