"""Strip the retired ``has_jersey`` key from saved export field selections.

``DataConnection.selected_fields`` is a JSON list of raw field keys, so removing the choice
from ``USER_FIELDS`` leaves any connection that had it ticked pointing at a field that no
longer exists. Nothing raises -- the sync falls back to the raw key as the column heading and
an empty value -- so an untouched connection would quietly grow a blank "has_jersey" column.
"""

from django.db import migrations

FIELD = "has_jersey"


def strip_field(apps, schema_editor) -> None:
    """Remove the key from every stored selection.

    Args:
        apps: The historical app registry.
        schema_editor: Unused.

    """
    data_connection = apps.get_model("data_connection", "DataConnection")
    for connection in data_connection.objects.all().iterator():
        fields = connection.selected_fields or []
        if FIELD in fields:
            connection.selected_fields = [f for f in fields if f != FIELD]
            connection.save(update_fields=["selected_fields"])


class Migration(migrations.Migration):
    """Drop the retired key from saved selections."""

    dependencies = [("data_connection", "0005_dataconnection_auto_sync")]

    # Irreversible only in the sense that the key is not put back: the field it named is gone,
    # so restoring it would recreate the blank column this removes.
    operations = [migrations.RunPython(strip_field, migrations.RunPython.noop)]
