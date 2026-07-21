"""Repoint TttPlan.route to zwift_data.ZwiftRoute and retire the old Route/Segment.

The canonical ``zwift_data`` dataset is now the single source of truth for routes and
segments. Existing plans keep their route selection where the old route maps to a
canonical route by name + world; unmatched ones fall back to NULL (SET_NULL). Runs after
the ladder repoint (``ladder_planner.0005``) so nothing FKs the old Route when it drops.
"""

import django.db.models.deletion
from django.db import migrations, models


def forward(apps, schema_editor):
    """Copy each plan's old-route selection to the new ZwiftRoute FK by name+world."""
    TttPlan = apps.get_model("ttt_planner", "TttPlan")
    Route = apps.get_model("ttt_planner", "Route")
    ZwiftRoute = apps.get_model("zwift_data", "ZwiftRoute")
    zr_by_key = {(z.name.lower(), (z.world or "").lower()): z.pk for z in ZwiftRoute.objects.all()}
    for plan in TttPlan.objects.exclude(route__isnull=True):
        old = Route.objects.filter(pk=plan.route_id).first()
        if old is None:
            continue
        plan.route_zr_id = zr_by_key.get((old.name.lower(), (old.world or "").lower()))
        plan.save(update_fields=["route_zr"])


def backward(apps, schema_editor):
    """No-op reverse: the old FK is restored empty (mapping is not reversible)."""


class Migration(migrations.Migration):
    dependencies = [
        ("ttt_planner", "0021_delete_routegpx"),
        ("ladder_planner", "0005_repoint_route_to_zwiftroute"),
        ("zwift_data", "0002_zwiftroute_recommended_laps_zwiftroute_supports_laps_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="tttplan",
            name="route_zr",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ttt_plans",
                to="zwift_data.zwiftroute",
                help_text="Selected route",
            ),
        ),
        migrations.RunPython(forward, backward),
        migrations.RemoveField(model_name="tttplan", name="route"),
        migrations.RenameField(model_name="tttplan", old_name="route_zr", new_name="route"),
        migrations.RemoveField(model_name="route", name="segments"),
        migrations.DeleteModel(name="Route"),
        migrations.DeleteModel(name="Segment"),
    ]
