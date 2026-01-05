# Race Ready Verification

Users can achieve "Race Ready" status by completing verification requirements. This status gates participation in official team races.

## Requirements

A user is race ready (`User.is_race_ready` property) when they have BOTH:

1. **Weight (Full) verification** - A verified `RaceReadyRecord` of type `weight_full` that is not expired
2. **Height verification** - A verified `RaceReadyRecord` of type `height` that is not expired

## Verification Types

| Type | Description | Default Validity |
|------|-------------|------------------|
| `weight_full` | Full weight verification with scale photo | 180 days |
| `weight_light` | Light weight verification | 30 days |
| `height` | Height verification | Forever (0 days) |
| `power` | Power verification | 365 days |

Validity periods are configurable via Constance settings:
- `WEIGHT_FULL_DAYS`
- `WEIGHT_LIGHT_DAYS`
- `HEIGHT_VERIFICATION_DAYS` (0 = never expires)
- `POWER_VERIFICATION_DAYS`

## Verification Flow

1. **User submits record**: User uploads a verification photo (weight, height, or power) via the web app
2. **Pending status**: Record starts in `pending` status
3. **Captain review**: Team captains (`team_captain` permission) review and verify/reject records
4. **Expiration**: Verified records expire based on configurable timeframes

## RaceReadyRecord Model

| Field | Description |
|-------|-------------|
| `user` | The user this record belongs to |
| `verify_type` | Type of verification (weight_full, weight_light, height, power) |
| `status` | pending, verified, or rejected |
| `image` | Uploaded verification photo |
| `value` | Optional numeric value (weight in kg, height in cm, etc.) |
| `verified_by` | User who verified/rejected the record |
| `verified_at` | When the record was verified |
| `notes` | Optional notes from verifier |
| `date_created` | When the record was submitted |

## Race Ready Discord Role

When a user's `is_race_ready` status is True, the Discord bot automatically assigns them a configured role.

### Configuration

Set `RACE_READY_ROLE_ID` in Django admin (set to `0` to disable).

### How It Works

1. User runs `/my_profile` or `/sync_my_roles` in Discord
2. API returns `is_race_ready` status and `race_ready_role_id`
3. Bot adds/removes the Discord role based on current status

### Bot Requirements

- Bot needs `Manage Roles` permission
- The race ready role must be below the bot's highest role in the hierarchy

## API Response

The `/my_profile` and `/sync_user_roles` endpoints return:

```json
{
  "is_race_ready": true,
  "race_ready_role_id": "1234567890123456789"
}
```

## Team Roster

The team roster view (`/team/roster/`) displays:
- Race ready status for each team member
- Filter option to show only race ready members
- Links to verification records

## Data Export

The `data_connection` module supports exporting `race_ready` status to Google Sheets along with other user data.

## Permissions

| Action | Required Permission |
|--------|---------------------|
| Submit verification record | Any authenticated user |
| View pending records | `team_captain` or `vice_captain` |
| Verify/reject records | `approve_verification` |

**Configuration**: Set `PERM_APPROVE_VERIFICATION_ROLES` in Django admin with Discord role IDs that can approve/reject records.
