# Permissions

Permissions are granted via Discord roles configured in Django admin (Constance settings).

## Permission Check Order

The system checks permissions in this order:

1. **Superusers** - Always have all permissions
2. **Manual overrides** - Explicit grant/revoke in `User.permission_overrides`
3. **Discord roles** - Matched against Constance permission settings
4. **Legacy app roles** - Backward compatibility via `User.roles`

## Available Permissions

| Permission | Description |
|------------|-------------|
| `app_admin` | Full application admin |
| `team_captain` | Team captain role |
| `vice_captain` | Vice captain role |
| `link_admin` | Can create, edit and delete team links |
| `membership_admin` | Membership management |
| `racing_admin` | Racing management |
| `team_member` | Required for most pages; without it users can only see index and their profile |
| `race_ready` | Race ready status |
| `approve_verification` | Can approve/reject verification records |
| `data_connection` | Access to Google Sheets data exports |
| `pages_admin` | Can create and manage CMS pages |

## Configuration

Configure permission mappings in Django admin at `/admin/constance/config/` under "Permission Mappings":

| Setting | Description |
|---------|-------------|
| `PERM_APP_ADMIN_ROLES` | Discord role IDs that grant app admin |
| `PERM_TEAM_CAPTAIN_ROLES` | Discord role IDs that grant team captain |
| `PERM_VICE_CAPTAIN_ROLES` | Discord role IDs that grant vice captain |
| `PERM_LINK_ADMIN_ROLES` | Discord role IDs that grant link admin |
| `PERM_MEMBERSHIP_ADMIN_ROLES` | Discord role IDs that grant membership admin |
| `PERM_RACING_ADMIN_ROLES` | Discord role IDs that grant racing admin |
| `PERM_TEAM_MEMBER_ROLES` | Discord role IDs for team members |
| `PERM_RACE_READY_ROLES` | Discord role IDs for race ready status |
| `PERM_APPROVE_VERIFICATION_ROLES` | Discord role IDs that can approve/reject verification records |
| `PERM_DATA_CONNECTION_ROLES` | Discord role IDs that can access data exports |
| `PERM_PAGES_ADMIN_ROLES` | Discord role IDs that can manage CMS pages |

**Format**: JSON array of Discord role IDs, e.g., `["1234567890123456789", "9876543210987654321"]`

## Usage in Views

### Using the Decorator

```python
from django.contrib.auth.decorators import login_required
from apps.accounts.decorators import discord_permission_required

# Single permission
@login_required
@discord_permission_required("team_captain")
def verify_record(request):
    ...

# Multiple permissions (OR logic - user needs ANY)
@login_required
@discord_permission_required(["team_captain", "vice_captain"])
def view_records(request):
    ...
```

**Note**: The decorator raises `PermissionDenied` (403) for authenticated users who lack permission, rather than redirecting to login. This prevents redirect loops. A custom `templates/403.html` provides a user-friendly error page.

### Using User Methods

```python
# Check permission directly
if request.user.has_permission("team_captain"):
    ...

# Property shortcuts
if request.user.is_team_captain:
    ...

if request.user.is_app_admin:
    ...
```

## Manual Permission Overrides

Set in Django admin User edit page under "Permissions" fieldset:

```json
{"team_captain": true}   // Grant without Discord role
{"team_captain": false}  // Revoke despite Discord role
```

## Keeping Roles in Sync

Discord roles are synced to the User model via:

1. **User command**: `/sync_my_roles` - User syncs their own roles
2. **Admin command**: `/sync_roles` - Admin syncs all guild roles
3. **API endpoint**: `POST /api/dbot/sync_user_roles/{discord_id}` - Called by Discord bot
4. **Automatic**: Role sync cog syncs on role changes and periodically

The user's `discord_roles` field stores `{role_id: role_name}` mapping from Discord.

## Troubleshooting

### User has the Discord role but no permission

1. Check that the role ID is in the correct `PERM_*_ROLES` setting
2. Ensure the user has synced their roles (`/sync_my_roles`)
3. Check for manual overrides that might revoke the permission

### Permission check returns 403

1. Verify the user is authenticated
2. Check they have one of the required permissions
3. For `team_member` permission, ensure they have a team member Discord role
