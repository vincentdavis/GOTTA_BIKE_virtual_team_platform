"""Central registry of permissions and the views they control.

When adding a new view with permission checks, update this registry.
This is used to display help tooltips on the permission config page.
"""

from apps.accounts.models import Permissions

PERMISSION_REGISTRY: dict[str, dict] = {
    Permissions.APP_ADMIN: {
        "name": "App Admin",
        "description": "Full application administration access",
        "views": [
            "/site/config/ - Site configuration settings",
        ],
    },
    Permissions.TEAM_CAPTAIN: {
        "name": "Team Captain",
        "description": "Team captain leadership role",
        "views": [
            "(Currently no view restrictions - used for role identification)",
        ],
    },
    Permissions.VICE_CAPTAIN: {
        "name": "Vice Captain",
        "description": "Vice captain leadership role",
        "views": [
            "(Currently no view restrictions - used for role identification)",
        ],
    },
    Permissions.LINK_ADMIN: {
        "name": "Link Admin",
        "description": "Manage team links",
        "views": [
            "(Currently no view restrictions - used for role identification)",
        ],
    },
    Permissions.MEMBERSHIP_ADMIN: {
        "name": "Membership Admin",
        "description": "Review and manage membership applications",
        "views": [
            "/team/membership-review/ - Membership review dashboard",
            "/team/applications/ - Membership application list",
            "/team/applications/{uuid}/ - Individual application review",
            "/team/discord-review/ - Discord guild member review",
        ],
    },
    Permissions.RACING_ADMIN: {
        "name": "Racing Admin",
        "description": "Manage racing-related settings",
        "views": [
            "(Currently no view restrictions - used for role identification)",
        ],
    },
    Permissions.TEAM_MEMBER: {
        "name": "Team Member",
        "description": "Required for most pages - basic team access",
        "views": [
            "/team/roster/ - Team roster",
            "/team/links/ - Team links",
            "/team/links/submit/ - Submit new team link",
            "/team/links/{id}/edit/ - Edit team link",
            "/team/verification/ - Verification records",
            "/team/verification/{id}/ - Verification detail",
            "/team/youtube/ - YouTube channels",
            "/team/performance-review/ - Performance review",
            "/user/profile/{id}/ - View teammate profiles",
        ],
    },
    Permissions.RACE_READY: {
        "name": "Race Ready",
        "description": "Eligible to participate in official races",
        "views": [
            "(Status indicator - no view restrictions)",
        ],
    },
    Permissions.APPROVE_VERIFICATION: {
        "name": "Approve Verification",
        "description": "Can approve or reject verification records",
        "views": [
            "/team/verification/{id}/ - Approve/reject verification submissions",
        ],
    },
    Permissions.DATA_CONNECTION: {
        "name": "Data Connection",
        "description": "Access Google Sheets data exports",
        "views": [
            "/data-connections/ - Data connections list",
            "/data-connections/create/ - Create new connection",
            "/data-connections/{id}/edit/ - Edit connection",
            "/data-connections/{id}/delete/ - Delete connection",
            "/data-connections/{id}/sync/ - Sync data to sheet",
        ],
    },
}


def get_permission_help(constance_key: str) -> dict | None:
    """Get permission help data from a Constance key like PERM_APP_ADMIN_ROLES.

    Args:
        constance_key: The Constance setting key (e.g., "PERM_APP_ADMIN_ROLES")

    Returns:
        Dict with name, description, views or None if not found.

    """
    # Convert PERM_APP_ADMIN_ROLES -> app_admin
    if not constance_key.startswith("PERM_") or not constance_key.endswith("_ROLES"):
        return None

    permission_name = constance_key[5:-6].lower()  # Strip PERM_ and _ROLES
    return PERMISSION_REGISTRY.get(permission_name)
