# Profile Completion Requirement

All users must complete their profile before accessing most pages in the application. This applies to both new and existing users.

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

## How It Works

1. **New users**: After Discord OAuth login, redirected to profile edit page
2. **Existing users**: If profile is incomplete, redirected to profile edit page on any request
3. **Superusers**: Always exempt from profile completion requirement
4. **API endpoints**: Always exempt from profile completion check

## Middleware

The `ProfileCompletionMiddleware` (`apps/accounts/middleware.py`) enforces profile completion:

```python
# Exempt URL patterns
EXEMPT_URL_PATTERNS = [
    r"^/user/profile/edit/$",        # Profile edit page
    r"^/user/profile/verify-zwift/$", # Zwift verification
    r"^/user/profile/unverify-zwift/$",
    r"^/accounts/",                   # Auth URLs
    r"^/api/",                        # API endpoints
    r"^/admin/",                      # Django admin
    r"^/static/",                     # Static files
    r"^/media/",                      # Media files
    r"^/__debug__/",                  # Debug toolbar
    r"^/__reload__/",                 # Browser reload
    r"^/m/",                          # Magic links
]
```

## User Model Properties

### `is_profile_complete`

Returns `True` if all required fields are filled AND Zwift is verified:

```python
if user.is_profile_complete:
    # User can access the full application
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

This is used by the profile edit template to show which fields are missing.

## Profile Edit Page

The profile edit page (`/user/profile/edit/`) shows:

1. **Warning banner** when profile is incomplete
2. **Status badges** showing which fields are complete/missing
3. **All required fields** with required indicators (`*`)
4. **Zwift verification section** for linking and verifying Zwift account

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
