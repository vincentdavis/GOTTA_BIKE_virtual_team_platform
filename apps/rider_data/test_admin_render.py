"""Render guards on the admin pages.

The rider-profile admin shipped with a 500 that no existing test caught, because the tests
checked *configuration* -- readonly fields, add permission -- and never rendered a page with
a row in it. The failure only appears once there is data: format_html with no interpolation
argument raises TypeError in Django 6, and Django's system checks do not see it because it
happens at render time.

So these render. Every admin page this project registers should be openable with data in it,
which is a cheaper guarantee than it sounds and catches a whole class of display-helper bugs.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.rider_data.models import RiderProfile
from conftest import _make_user


@pytest.fixture
def admin_client_staff(client, user_model):
    staff = _make_user(user_model, username="render_admin", is_staff=True, is_superuser=True)
    client.force_login(staff)
    return client


@pytest.mark.django_db
def test_the_rider_profile_changelist_renders_with_rows(admin_client_staff):
    """Both branches of the last-race column: a real date, and the 'none' case that 500'd."""
    RiderProfile.objects.create(zwid=1, name="Has raced", fetched_at=timezone.now(), last_race_at=timezone.now())
    RiderProfile.objects.create(zwid=2, name="Never raced", fetched_at=timezone.now(), last_race_at=None)

    response = admin_client_staff.get(reverse("admin:rider_data_riderprofile_changelist"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "not evictable" in body, "the null-anchor case must render, not raise"


@pytest.mark.django_db
def test_the_rider_profile_change_page_renders(admin_client_staff):
    """The detail page renders the payload and provenance blocks."""
    RiderProfile.objects.create(
        zwid=3, name="Detail", fetched_at=timezone.now(),
        payload={"power": {"curve_w": {"5": 900}}}, sources={"zwiftpower": {"present": True}},
        has_account={"zwiftpower": True},
    )

    response = admin_client_staff.get(reverse("admin:rider_data_riderprofile_change", args=[3]))

    assert response.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize(
    "model_label",
    ["cms.Page", "rider_data.RiderProfile"],
)
def test_registered_admin_changelists_render(admin_client_staff, model_label, user_model):
    """A changelist that 500s on real data is invisible until somebody opens it.

    cms.Page carried the same format_html defect as rider_data did, unnoticed, which is why
    this is parameterised rather than written once.
    """
    from django.apps import apps as django_apps

    model = django_apps.get_model(model_label)
    if model is RiderProfile:
        RiderProfile.objects.create(zwid=9, name="X", fetched_at=timezone.now(), last_race_at=None)
    else:
        from apps.cms.models import Page

        Page.objects.create(title="Pub", slug="pub", content="x", status=Page.Status.PUBLISHED)
        Page.objects.create(title="Drf", slug="drf", content="x", status=Page.Status.DRAFT)

    opts = model._meta
    url = reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")

    assert admin_client_staff.get(url).status_code == 200
