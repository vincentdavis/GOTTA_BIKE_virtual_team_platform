"""Seed a starter set of popular WTRL TTT routes."""

from django.db import migrations

# (name, world, distance_km, elevation_m)
ROUTES = [
    ("Tempus Fugit", "Watopia", 17.3, 16),
    ("Tick Tock", "Watopia", 19.2, 59),
    ("Greatest London Flat", "London", 17.5, 117),
    ("Greater London Flat", "London", 14.3, 99),
    ("Flat Route", "Watopia", 10.3, 46),
    ("Volcano Flat", "Watopia", 12.3, 49),
    ("Volcano Circuit CCW", "Watopia", 4.1, 21),
    ("Sand And Sequoias", "Watopia", 18.6, 67),
    ("The Fan Flats", "Makuri Islands", 10.5, 18),
    ("Three Village Loop", "Makuri Islands", 10.7, 56),
    ("Castle to Castle", "Makuri Islands", 24.5, 178),
    ("Flatland Loop", "France", 14.7, 36),
    ("R.G.V.", "France", 14.1, 95),
    ("Glasgow Worlds Course", "Scotland", 8.8, 109),
    ("Downtown Dolphin", "Crit City", 1.9, 14),
]


def seed_routes(apps, schema_editor):
    """Insert starter routes if they are not already present."""
    route_model = apps.get_model("ttt_planner", "Route")
    for name, world, distance_km, elevation_m in ROUTES:
        route_model.objects.get_or_create(
            name=name,
            defaults={"world": world, "distance_km": distance_km, "elevation_m": elevation_m},
        )


def unseed_routes(apps, schema_editor):
    """Remove the seeded routes on reverse migration."""
    route_model = apps.get_model("ttt_planner", "Route")
    route_model.objects.filter(name__in=[r[0] for r in ROUTES]).delete()


class Migration(migrations.Migration):
    """Seed starter TTT routes."""

    dependencies = [
        ("ttt_planner", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_routes, unseed_routes),
    ]
