"""Decode HTML character references in stored zwiftracing names.

zwiftracing sends names with HTML entities (``&quot;``, ``&#128062;``), and rows written
before ``tasks._clean_str`` hold them as sent, which a template auto-escapes again so the
entity itself shows on the page. Decode them in place. Only values containing ``&`` are
read and only rows that change are written, so a plain ampersand stays as it is. The club
sweep would put every current member right within four hours anyway; this also covers the
riders it no longer updates.

Forward-only; the reverse is a no-op (re-encoding isn't well-defined). The twin of zauth's
``zrapp/0003``.
"""

import html

from django.db import migrations
from django.db.models import Q

_FIELDS = ["name", "club_name"]
_BATCH = 500


def _decode_entities(apps, schema_editor):
    """Rewrite each name field, decoding HTML entities where they changed.

    Args:
        apps: The historical app registry.
        schema_editor: The migration schema editor (unused).

    """
    model = apps.get_model("zwiftracing", "ZRRider")
    contains_amp = Q()
    for field in _FIELDS:
        contains_amp |= Q(**{f"{field}__contains": "&"})
    changed = []
    for obj in model.objects.filter(contains_amp).iterator(chunk_size=_BATCH):
        dirty = False
        for field in _FIELDS:
            raw = getattr(obj, field) or ""
            decoded = html.unescape(raw)
            if decoded != raw:
                setattr(obj, field, decoded)
                dirty = True
        if dirty:
            changed.append(obj)
    for start in range(0, len(changed), _BATCH):
        model.objects.bulk_update(changed[start : start + _BATCH], _FIELDS)


class Migration(migrations.Migration):
    dependencies = [
        ("zwiftracing", "0003_add_seed_velo_fields"),
    ]
    operations = [
        migrations.RunPython(_decode_entities, migrations.RunPython.noop),
    ]
