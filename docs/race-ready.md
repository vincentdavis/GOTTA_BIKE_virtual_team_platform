# Race Ready Verification

Users can achieve "Race Ready" status by completing verification requirements. This status gates participation in
official team races.

## Terminology

"Race Verified" and "Race Ready" have the same meaning and are used interchangeably. The UI/UX uses "Race Verified"
while the backend code uses "Race Ready" (e.g., `is_race_ready`, `RaceReadyRecord`, `RACE_READY_ROLE_ID`).

## Race Ready Requirements

A user is race ready (`User.is_race_ready` property) when they have **ALL** verification types required for their
ZwiftPower category (see Category-Based Requirements below). The `is_race_ready` property dynamically checks:

1. Looks up the user's required verification types based on their ZwiftPower division
2. Checks that the user has a verified, non-expired `RaceReadyRecord` for **each** required type
3. Returns `True` only if all required types are satisfied

## Verification Types

| Type           | Description                               | Default Validity |
|----------------|-------------------------------------------|------------------|
| `weight_full`  | Full weight verification with scale photo | 180 days         |
| `weight_light` | Light weight verification                 | 30 days          |
| `height`       | Height verification                       | Forever (0 days) |
| `power`        | Power verification                        | 365 days         |

Validity periods are configurable via Constance settings:

- `WEIGHT_FULL_DAYS` (default: 180)
- `WEIGHT_LIGHT_DAYS` (default: 30)
- `HEIGHT_VERIFICATION_DAYS` (default: 0 = never expires)
- `POWER_VERIFICATION_DAYS` (default: 365)

A configurable Markdown message can be displayed at the top of the verification form via the `VERIFICATION_FORM_MESSAGE` Constance setting (in the "Verification Settings" group). When set, it renders below the card title on `/user/verification/`.

### Verification Emojis

Custom emoji/icon images for verification status are stored on the `SiteSettings` model (not Constance, since they
require file uploads):

| Field                  | Description                          |
|------------------------|--------------------------------------|
| `not_verified_emoji`   | Icon for not-verified status         |
| `verified_emoji`       | Icon for verified status             |
| `extra_verified_emoji` | Icon for extra-verified status       |

Upload via `/site/config/` "Site Images" section or Django admin (`/admin/gotta_bike_platform/sitesettings/`).
Accessible in templates via `site_settings.not_verified_emoji`, `site_settings.verified_emoji`, and
`site_settings.extra_verified_emoji`. Recommended size: 64x64 PNG with transparency.

When uploaded, `verified_emoji` and `not_verified_emoji` replace the default colored badges/SVGs for race verified
status across all pages:

| Template                       | Location                                  |
|--------------------------------|-------------------------------------------|
| `base.html`                    | Header status text and user dropdown menu |
| `accounts/verification.html`   | Verification page title icon              |
| `accounts/public_profile.html` | Public profile Race Verified row          |
| `team/roster.html`             | Roster Race Verified column               |
| `events/event_detail.html`     | Signup list and squad member list         |
| `events/event_form.html`       | Event edit signup list                    |

If no emoji image is uploaded, the original badges and SVG icons are shown as fallback.

## Category-Based Requirements

The verification types **required** for race ready status depend on the user's ZwiftPower category. This is configured
via the `CATEGORY_REQUIREMENTS` Constance setting.

### How It Works

1. User's ZwiftPower record is looked up by their `zwid`
2. Category is determined: `divw` for female users, `div` for everyone else
3. Required verification types are determined from `CATEGORY_REQUIREMENTS[category]`
4. If no ZwiftPower record exists, defaults to `["weight_light", "height"]`
5. User must have **ALL** required types verified (non-expired) to be race ready

### Default Configuration

```json
{
  "5": ["weight_full", "height", "power"],
  "10": ["weight_full", "height"],
  "20": ["weight_full", "height"],
  "30": ["weight_full", "height"],
  "40": ["weight_light", "height"],
  "50": ["weight_light", "height"]
}
```

| ZP Division | Category | Required Verification Types       |
|-------------|----------|-----------------------------------|
| 5           | A+       | weight_full, height, power        |
| 10          | A        | weight_full, height               |
| 20          | B        | weight_full, height               |
| 30          | C        | weight_full, height               |
| 40          | D        | weight_light, height              |
| 50          | E        | weight_light, height              |
| (none)      | -        | weight_light, height (default)    |

### Implementation

The `get_user_verification_types(user)` function in `apps/team/services.py` returns the required types.
This function is used by:
- `User.is_race_ready` property to check if user meets all requirements
- `RaceReadyRecordForm` to filter the dropdown choices when submitting

## Record Status

Each verification record has one of three statuses:

| Status     | Description                           |
|------------|---------------------------------------|
| `pending`  | Awaiting review by an approver        |
| `verified` | Approved and valid (until expiration) |
| `rejected` | Rejected with a reason                |

## Verification Flow

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  User submits   │────▶│    Pending      │────▶│    Verified     │
│  verification   │     │  (awaiting      │     │  (valid until   │
│  record         │     │   review)       │     │   expiration)   │
└─────────────────┘     └────────┬────────┘     └────────┬────────┘
                                 │                       │
                                 ▼                       ▼
                        ┌─────────────────┐     ┌─────────────────┐
                        │    Rejected     │     │    Expired      │
                        │  (with reason)  │     │  (re-verify)    │
                        └─────────────────┘     └─────────────────┘
```

1. **User submits record**: User uploads verification evidence (photo, video, or URL) via `/user/verification/submit/`
2. **Pending status**: Record starts in `pending` status
3. **Approver review**: Users with `approve_verification` permission review at `/team/verification/`
4. **Record date editing**: Reviewers can edit the record date before verifying/rejecting (e.g., to correct a date)
5. **Verification or Rejection**: Approver verifies (with optional notes) or rejects (with required reason)
6. **Deletion**: Users with `performance_verification_team` or `app_admin` permission can delete any verification record (any status)
7. **Media cleanup**: On verification, uploaded media files are deleted for privacy
8. **Expiration**: Verified records expire based on validity period; user must re-verify

## Permissions

| Action                         | Required Permission                              | Constance Setting                              |
|--------------------------------|--------------------------------------------------|------------------------------------------------|
| Submit verification record     | Any authenticated team member                    | -                                              |
| View verification records list | `approve_verification`                           | `PERM_APPROVE_VERIFICATION_ROLES`              |
| View individual record details | `approve_verification`                           | `PERM_APPROVE_VERIFICATION_ROLES`              |
| Verify/reject records          | `approve_verification`                           | `PERM_APPROVE_VERIFICATION_ROLES`              |
| Edit record date during review | `approve_verification`                           | `PERM_APPROVE_VERIFICATION_ROLES`              |
| Change status of any record    | `performance_verification_team` or `app_admin`   | `PERM_PERFORMANCE_VERIFICATION_TEAM_ROLES` / `PERM_APP_ADMIN_ROLES` |
| Delete any verification record | `performance_verification_team` or `app_admin`   | `PERM_PERFORMANCE_VERIFICATION_TEAM_ROLES` / `PERM_APP_ADMIN_ROLES` |

**Note**: Superusers always have all permissions.

### Configuration

Set `PERM_APPROVE_VERIFICATION_ROLES` in Django admin (`/admin/constance/config/`) with a JSON array of Discord role
IDs:

```json
[
  "1234567890123456789",
  "9876543210987654321"
]
```

## RaceReadyRecord Model

| Field              | Type                 | Description                                            |
|--------------------|----------------------|--------------------------------------------------------|
| `user`             | ForeignKey           | The user this record belongs to                        |
| `verify_type`      | CharField            | Type: `weight_full`, `weight_light`, `height`, `power` |
| `media_type`       | CharField            | Evidence type: `video`, `photo`, `link`, `other`       |
| `media_file`       | FileField            | Uploaded photo/video (deleted on verification)         |
| `url`              | URLField             | External URL (YouTube, Vimeo, image link)              |
| `weight`           | DecimalField         | Weight in kg (for weight verifications)                |
| `height`           | PositiveIntegerField | Height in cm (for height verification)                 |
| `ftp`              | PositiveIntegerField | FTP in watts (for power verification)                  |
| `status`           | CharField            | `pending`, `verified`, `rejected`                      |
| `reviewed_by`      | ForeignKey           | User who reviewed the record                           |
| `reviewed_date`    | DateTimeField        | When the record was reviewed                           |
| `review_note`      | TextField            | Optional reviewer note (for any status change)         |
| `notes`            | TextField            | Optional notes from user                               |
| `date_created`     | DateTimeField        | When the record was submitted                          |

### Model Properties

| Property          | Returns  | Description                                 |
|-------------------|----------|---------------------------------------------|
| `is_verified`     | bool     | True if status is verified                  |
| `is_rejected`     | bool     | True if status is rejected                  |
| `is_pending`      | bool     | True if status is pending                   |
| `validity_days`   | int      | Days until expiration (0 = never)           |
| `expires_date`    | datetime | Expiration date (None if never expires)     |
| `is_expired`      | bool     | True if verification has expired            |
| `days_remaining`  | int      | Days until expiration (negative if expired) |
| `validity_status` | str      | Human-readable status string                |

## API Verification Status

The `/api/dbot/my_profile` and `/api/dbot/teammate_profile/{zwid}` endpoints return verification status for each type.

### Status Logic

| Scenario                   | `verified` | `status`                            | `has_pending`  |
|----------------------------|------------|-------------------------------------|----------------|
| Valid verified record      | `true`     | "Valid (X days)" or "Never expires" | `true`/`false` |
| Expired, no pending record | `true`     | "Expired"                           | `false`        |
| Expired + pending record   | `false`    | "Pending (expired)"                 | `true`         |
| No verified, has pending   | `false`    | "Pending"                           | `true`         |
| No records                 | `false`    | "No record"                         | `false`        |

### Example Response

```json
{
  "verification": {
    "weight_full": {
      "verified": true,
      "verified_date": "2024-01-15T10:30:00+00:00",
      "days_remaining": 120,
      "is_expired": false,
      "status": "Valid (120 days)",
      "has_pending": false
    },
    "height": {
      "verified": true,
      "verified_date": "2024-01-10T08:00:00+00:00",
      "days_remaining": null,
      "is_expired": false,
      "status": "Never expires",
      "has_pending": false
    },
    "weight_light": {
      "verified": false,
      "verified_date": null,
      "days_remaining": null,
      "is_expired": false,
      "status": "No record",
      "has_pending": false
    },
    "power": {
      "verified": false,
      "verified_date": "2023-06-01T12:00:00+00:00",
      "days_remaining": -30,
      "is_expired": true,
      "status": "Pending (expired)",
      "has_pending": true,
      "pending_date": "2024-01-20T14:00:00+00:00"
    }
  },
  "is_race_ready": true
}
```

## Race Ready Discord Role

When a user's `is_race_ready` status is True, the Discord bot can automatically assign them a configured role.

### Configuration

Set `RACE_READY_ROLE_ID` in Django admin with the Discord role ID (set to `0` to disable).

### How It Works

1. User runs `/my_profile` or `/sync_my_roles` in Discord
2. API returns `is_race_ready` status and `race_ready_role_id`
3. Bot adds/removes the Discord role based on current status

### Bot Requirements

- Bot needs `Manage Roles` permission
- The race ready role must be below the bot's highest role in the hierarchy

## Web App URLs

| URL                          | Description                                    |
|------------------------------|------------------------------------------------|
| `/user/verification/submit/` | Submit a new verification record               |
| `/team/verification/`        | List all verification records (approvers only) |
| `/team/verification/{id}/`   | View/approve/reject a record (approvers only)  |
| `/team/roster/`              | Team roster with race ready filter             |

## Team Roster

The team roster view (`/team/roster/`) displays:

- Race ready status badge for each team member
- Filter option to show only race ready members
- Sortable by race ready status

## Data Export

The `data_connection` module supports exporting `race_ready` status to Google Sheets along with other user data.
