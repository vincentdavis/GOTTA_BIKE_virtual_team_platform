# Profile Completion

Users are encouraged to complete their profile but are **not blocked** from accessing the application. Instead, a warning banner is displayed at the top of every page for users with incomplete profiles.

## Required Fields

| Field | Description |
|-------|-------------|
| `first_name` | User's first name |
| `last_name` | User's last name |
| `birth_year` | Year of birth (validated: 1900 to current_year - 13) |
| `gender` | Gender (male/female/other) |
| `timezone` | User's timezone (e.g., "America/New_York") |
| `country` | Country of residence |
| `zwid_verified` | Zwift account must be verified |

## Warning Banner

When a user's profile is incomplete, a red warning banner is displayed at the top of every page (defined in `theme/templates/base.html`). The banner shows:

- Which specific fields are missing (as badges)
- A link to the profile edit page

This approach allows users to explore the app while being reminded to complete their profile.

## User Model Properties

### `is_profile_complete`

Returns `True` if all required fields are filled AND Zwift is verified:

```python
if user.is_profile_complete:
    # User has completed all required fields
    ...
```

### `profile_completion_status`

Returns a dictionary with the completion status of each required field:

```python
status = user.profile_completion_status
# Returns:
# {
#     "first_name": True,
#     "last_name": True,
#     "birth_year": True,
#     "gender": False,      # Missing
#     "timezone": True,
#     "country": True,
#     "zwid_verified": False # Not verified
# }
```

This is used by the base template to show which fields are missing in the warning banner.

## Profile Edit Page

The profile edit page (`/user/profile/edit/`) shows:

1. **Status badges** showing which fields are complete/missing
2. **All required fields** with required indicators (`*`)
3. **Zwift verification section** for linking and verifying Zwift account

## Zwift Verification

Users must verify their Zwift account by:

1. Clicking "Verify Zwift Account" on the profile edit page
2. Entering their Zwift email and password
3. The system fetches their Zwift ID and marks the account as verified

**Note**: Zwift credentials are not stored. They are only used once to fetch the Zwift ID.

## Form Validation

The `ProfileForm` enforces:

- All required fields must be filled
- Birth year must be between 1900 and (current year - 13)
- Gender must be one of: male, female, other

## Public User Profiles

Team members can view each other's profiles at `/user/profile/<user_id>/`. This feature allows teammates to learn about each other while respecting privacy boundaries.

### Access Requirements

- User must be logged in (`@login_required`)
- User must have `team_member` permission (`@team_member_required()`)
- Viewing your own profile redirects to the private profile page

### Privacy Settings

**Displayed on public profiles:**

| Category | Fields |
|----------|--------|
| Identity | First name, last name |
| Discord | Username, nickname, avatar |
| Location | City, country, timezone |
| Zwift | Verification status, ZwiftPower link |
| Racing | Race ready status, gender |
| Equipment | Trainer, power meter, dual recording, HR monitor |
| Social | All social media links |
| Team | Discord roles |

**Never displayed on public profiles:**

- Birth year
- Email address
- Emergency contact information

### Profile Links

User names are clickable links to public profiles in:

- Team roster page (`/team/roster/`)
- Membership review tables (both race and member views)

This allows team members to quickly look up information about their teammates from the roster.
