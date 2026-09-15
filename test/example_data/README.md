# Example API data

Sample payloads documenting the **shape** of the ZwiftPower and ZwiftRacing.app
responses the sync tasks consume. The model docstrings in
`apps/zwiftpower/models.py` and `apps/zwiftracing/models.py` point here.

## Generated, not captured

Everything here is invented. A few rows are enough to pin the shape, so the
fixtures are written by hand rather than saved from a live response:

- Riders are named `Test Rider 1` … `Test Rider 4`.
- Zwift IDs start at `9000001`, above the ID range in use (~1–8 million).
- The team/club is `TEST TEAM` / `TEST CLUB`, ID `99999`.

Keep it that way when you update them — `gotta_bike_platform/test_example_data_fixtures.py`
holds the line from both sides: the values must stay plainly invented and the
files small, and the payloads must still parse through the loaders that read
them, so neither the content nor the shape can drift unnoticed. See the
"Personal Data & GDPR" section of `CLAUDE.md`.

## Files

| File | Source |
| --- | --- |
| `zwiftpower/team_admin_api.json` | `api3.php?do=team_riders&id={team_id}` → `ZPTeamRiders` |
| `zwiftpower/team_results.json` | `api3.php?do=team_results&id={team_id}` → `ZPEvent` + `ZPRiderResults` |
| `zwiftracing/rider_api.json` | `GET /public/riders/{riderId}` → `ZRRider` |
| `zwiftracing/riders_api.json` | `POST /public/riders` (bulk) |
| `zwiftracing/club_api.json` | `GET /public/clubs/{id}` |
| `zwiftracing/API Error (429).json` | Rate-limit response body |
| `zwiftracing/ZwiftRacing.app Public APIs.md` | Vendor endpoint reference |

Coverage worth preserving when editing the fixtures: a blank 20-minute power
pair (`["", 0]`), a numeric rather than string weight, and a rider with no
30-day rating (`max30` carrying only `rating: 0` and `expires`).
