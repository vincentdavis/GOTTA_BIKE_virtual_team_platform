"""zwiftracing sends names HTML-encoded: they are decoded on the way in, and stored ones repaired.

Stored as sent, ``&quot;`` is auto-escaped again by every template and shows as itself, as it
did in the ladder planner (a nickname reading ``&quot;The Climber&quot;``).
"""

import importlib

import pytest
from django.apps import apps as django_apps

from apps.zwiftracing.models import ZRRider
from apps.zwiftracing.tasks import _map_rider_to_model

decode_migration = importlib.import_module("apps.zwiftracing.migrations.0004_decode_html_entities_in_names")

ENCODED = "Sam &quot;The Climber&quot; Lane &#128062;"
DECODED = 'Sam "The Climber" Lane \U0001f43e'


def test_the_sweep_stores_names_decoded():
    fields = _map_rider_to_model({"name": ENCODED, "club": {"id": 1, "name": "Rise &amp; Shine"}})

    assert fields["name"] == DECODED
    assert fields["club_name"] == "Rise & Shine"


def test_a_missing_name_stays_blank():
    fields = _map_rider_to_model({})

    assert fields["name"] == ""
    assert fields["club_name"] == ""


@pytest.mark.django_db
def test_the_migration_decodes_stored_names_and_leaves_plain_ampersands():
    ZRRider.objects.create(zwid=1, name=ENCODED, club_name="Rise &amp; Shine")
    ZRRider.objects.create(zwid=2, name="Rise & Shine")
    ZRRider.objects.create(zwid=3, name="Plain")

    decode_migration._decode_entities(django_apps, None)

    names = dict(ZRRider.objects.values_list("zwid", "name"))
    assert names == {1: DECODED, 2: "Rise & Shine", 3: "Plain"}
    assert ZRRider.objects.get(zwid=1).club_name == "Rise & Shine"
