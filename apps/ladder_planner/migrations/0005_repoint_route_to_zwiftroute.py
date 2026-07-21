"""Repoint LadderMatchup.route from ttt_planner.Route to zwift_data.ZwiftRoute.

Existing saved matchups keep their route selection where the old route maps to a
canonical route by name + world (case-insensitive); unmatched ones fall back to NULL
(the FK is SET_NULL and only prefills name/profile).
"""

import django.db.models.deletion
from django.db import migrations, models


def forward(apps, schema_editor):
    """Copy each matchup's old-route selection to the new ZwiftRoute FK by name+world."""
    LadderMatchup = apps.get_model("ladder_planner", "LadderMatchup")
    Route = apps.get_model("ttt_planner", "Route")
    ZwiftRoute = apps.get_model("zwift_data", "ZwiftRoute")
    zr_by_key = {(z.name.lower(), (z.world or "").lower()): z.pk for z in ZwiftRoute.objects.all()}
    for matchup in LadderMatchup.objects.exclude(route__isnull=True):
        old = Route.objects.filter(pk=matchup.route_id).first()
        if old is None:
            continue
        matchup.route_zr_id = zr_by_key.get((old.name.lower(), (old.world or "").lower()))
        matchup.save(update_fields=["route_zr"])


def backward(apps, schema_editor):
    """No-op reverse: the old FK is restored empty (mapping is not reversible)."""


class Migration(migrations.Migration):
    dependencies = [
        ("ladder_planner", "0004_laddermatchup_cda_coef"),
        ("ttt_planner", "0021_delete_routegpx"),
        ("zwift_data", "0002_zwiftroute_recommended_laps_zwiftroute_supports_laps_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="laddermatchup",
            name="route_zr",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="zwift_data.zwiftroute",
                help_text="Optional route picked from the canonical library (prefills name + profile)",
            ),
        ),
        migrations.RunPython(forward, backward),
        migrations.RemoveField(model_name="laddermatchup", name="route"),
        migrations.RenameField(model_name="laddermatchup", old_name="route_zr", new_name="route"),
    ]
