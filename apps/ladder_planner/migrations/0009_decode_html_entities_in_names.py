"""Decode HTML character references in stored zwiftracing names.

zwiftracing sends names with HTML entities (``&quot;``, ``&#128062;``), and rows written
before ``services.normalize.clean_text`` hold them as sent, which a template auto-escapes
again so the entity itself shows on the page. Decode them in place: each row's own name
columns and the copies in its ``zr_data`` snapshot. A matchup's snapshots are frozen, so
nothing else would ever put them right. Only rows with an ``&`` in a name, column or
snapshot, are read and only rows that change are written, so a plain ampersand stays as
it is.

Forward-only; the reverse is a no-op (re-encoding isn't well-defined). The twin of
``zwiftracing/0004`` and of zauth's ``zrapp/0003``.
"""

import html

from django.db import migrations
from django.db.models import Q

# Model name -> (name columns, whether it carries a zr_data snapshot).
_TARGETS = {
    "CachedRider": (["name", "club_name"], True),
    "LadderRider": (["name"], True),
    "CachedClub": (["name"], False),
}
# The snapshot keys holding names (services.normalize).
_SNAPSHOT_KEYS = ("name", "club_name")
_BATCH = 500


def _decoded(value):
    """Return ``value`` with HTML entities decoded, if it is a string.

    Returns:
        The decoded string, or ``value`` unchanged.

    """
    return html.unescape(value) if isinstance(value, str) else value


def _decode_entities(apps, schema_editor):
    """Rewrite each name, column and snapshot copy alike, decoding HTML entities.

    Args:
        apps: The historical app registry.
        schema_editor: The migration schema editor (unused).

    """
    for model_name, (columns, has_snapshot) in _TARGETS.items():
        model = apps.get_model("ladder_planner", model_name)
        contains_amp = Q()
        for column in columns:
            contains_amp |= Q(**{f"{column}__contains": "&"})
        if has_snapshot:
            for key in _SNAPSHOT_KEYS:
                contains_amp |= Q(**{f"zr_data__{key}__icontains": "&"})
        changed = []
        for obj in model.objects.filter(contains_amp).iterator(chunk_size=_BATCH):
            dirty = False
            for column in columns:
                raw = getattr(obj, column)
                decoded = _decoded(raw)
                if decoded != raw:
                    setattr(obj, column, decoded)
                    dirty = True
            if has_snapshot and isinstance(obj.zr_data, dict):
                for key in _SNAPSHOT_KEYS:
                    raw = obj.zr_data.get(key)
                    decoded = _decoded(raw)
                    if decoded != raw:
                        obj.zr_data[key] = decoded
                        dirty = True
            if dirty:
                changed.append(obj)
        fields = [*columns, "zr_data"] if has_snapshot else columns
        for start in range(0, len(changed), _BATCH):
            model.objects.bulk_update(changed[start : start + _BATCH], fields)


class Migration(migrations.Migration):
    dependencies = [
        ("ladder_planner", "0008_alter_laddermatchup_course_profile"),
    ]
    operations = [
        migrations.RunPython(_decode_entities, migrations.RunPython.noop),
    ]
