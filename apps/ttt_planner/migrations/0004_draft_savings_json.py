"""Convert TttPlan.draft_savings from a CSV CharField to a JSONField (list).

Uses a temp field + data copy + rename so it works on both SQLite (local) and
PostgreSQL (prod) without a varchar->jsonb cast, and parses any existing
comma-separated values into a list of fractions.
"""

from django.db import migrations, models


def _to_fractions(raw: str) -> list[float]:
    """Parse a stored CSV/percentage string into a list of savings fractions.

    Args:
        raw: The stored draft-savings string (e.g. ``"0, 23.3, 30"``).

    Returns:
        A list of savings fractions, empty if nothing parseable.

    """
    if not raw:
        return []
    values: list[float] = []
    for tok in raw.replace(";", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            values.append(float(tok))
        except ValueError:
            continue
    if not values:
        return []
    if max(values) > 1.0:  # stored as percentages
        values = [v / 100.0 for v in values]
    values = [min(max(v, 0.0), 0.95) for v in values]
    if values[0] > 1e-9:
        values = [0.0, *values]
    return values


def copy_forward(apps, schema_editor):
    """Copy the old CSV string into the new JSON list field."""
    plan_model = apps.get_model("ttt_planner", "TttPlan")
    for plan in plan_model.objects.all():
        plan.draft_savings_json = _to_fractions(plan.draft_savings or "")
        plan.save(update_fields=["draft_savings_json"])


def copy_backward(apps, schema_editor):
    """Reverse: render the JSON list back to a percentage CSV string."""
    plan_model = apps.get_model("ttt_planner", "TttPlan")
    for plan in plan_model.objects.all():
        vals = plan.draft_savings_json or []
        plan.draft_savings = ", ".join(f"{round(float(v) * 100, 1):g}" for v in vals)
        plan.save(update_fields=["draft_savings"])


class Migration(migrations.Migration):
    """Migrate draft_savings to JSON."""

    dependencies = [
        ("ttt_planner", "0003_tttplan_draft_savings"),
    ]

    operations = [
        migrations.AddField(
            model_name="tttplan",
            name="draft_savings_json",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(copy_forward, copy_backward),
        migrations.RemoveField(model_name="tttplan", name="draft_savings"),
        migrations.RenameField(model_name="tttplan", old_name="draft_savings_json", new_name="draft_savings"),
        migrations.AlterField(
            model_name="tttplan",
            name="draft_savings",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Per-plan aero draft savings fractions by wheel position, e.g. [0.0, 0.233, 0.30]. "
                "Index 0 is the front rider (no draft). Empty list uses the global TTT_DRAFT_SAVINGS default.",
            ),
        ),
    ]
