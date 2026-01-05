# Plan: Permissions Site Map Page

## Overview

Create a page that displays all views in the application along with their permission requirements. This acts as a "site map with permissions" for administrators and developers to understand access control across the application.

## Approach: Decorator Registry Pattern

The recommended approach is to modify the permission decorators to automatically register views and their requirements in a central registry. This ensures the permissions map is always accurate and requires no manual maintenance.

## Current State Analysis

### Decorators in Use (`apps/accounts/decorators.py`)

| Decorator | Description |
|-----------|-------------|
| `discord_permission_required(permission)` | Requires specific Discord-based permission(s) |
| `any_captain_required()` | Shortcut for team_captain OR vice_captain |
| `any_admin_required()` | Shortcut for any admin role |
| `team_member_required()` | Requires team_member permission |

### Current View Permission Patterns

| URL Pattern | View | Decorators | Extra Permission Logic |
|-------------|------|------------|----------------------|
| `/` | `home` | None | Public |
| `/about/` | `about` | None | Public |
| `/user/profile/` | `profile_view` | `@login_required` | - |
| `/user/profile/edit/` | `profile_edit` | `@login_required` | - |
| `/user/profile/delete/` | `profile_delete_confirm` | `@login_required` | - |
| `/user/profile/delete/confirm/` | `profile_delete` | `@login_required` | - |
| `/user/profile/verify-zwift/` | `verify_zwift` | `@login_required` | - |
| `/user/profile/unverify-zwift/` | `unverify_zwift` | `@login_required` | - |
| `/user/profile/race-ready/` | `submit_race_ready` | `@login_required` | - |
| `/team/roster/` | `team_roster_view` | `@login_required`, `@team_member_required()` | - |
| `/team/links/` | `team_links_view` | `@login_required`, `@team_member_required()` | - |
| `/team/links/submit/` | `submit_team_link_view` | `@login_required`, `@team_member_required()` | Inline: `is_link_admin` |
| `/team/links/<pk>/edit/` | `edit_team_link_view` | `@login_required`, `@team_member_required()` | Inline: `is_link_admin` |
| `/team/links/<pk>/delete/` | `delete_team_link_view` | `@login_required`, `@team_member_required()` | Inline: `is_link_admin` |
| `/team/verification/` | `verification_records_view` | `@login_required`, `@team_member_required()` | Inline: `is_any_captain` |
| `/team/verification/<pk>/` | `verification_record_detail_view` | `@login_required`, `@team_member_required()` | Inline: `is_any_captain`, actions: `is_team_captain` |
| `/data-connections/` | `connection_list` | `@login_required`, `@team_member_required()` | - |
| `/data-connections/create/` | `connection_create` | `@login_required`, `@team_member_required()` | - |
| `/data-connections/<pk>/edit/` | `connection_edit` | `@login_required`, `@team_member_required()` | - |
| `/data-connections/<pk>/delete/` | `connection_delete` | `@login_required`, `@team_member_required()` | - |
| `/data-connections/<pk>/sync/` | `connection_sync` | `@login_required`, `@team_member_required()` | - |
| `/admin/` | Django Admin | Superuser | - |

---

## Implementation Steps

### Step 1: Create Permission Registry Module

Create `apps/accounts/permission_registry.py`:

```python
"""Central registry for view permissions."""

from dataclasses import dataclass, field
from typing import ClassVar

@dataclass
class ViewPermission:
    """Represents a view's permission requirements."""

    view_name: str  # Fully qualified view name (e.g., "accounts:profile")
    url_pattern: str  # URL pattern (e.g., "/user/profile/")
    requires_login: bool = False
    permissions: list[str] = field(default_factory=list)  # Discord permissions required (OR logic)
    inline_permissions: list[str] = field(default_factory=list)  # Permissions checked in view body
    description: str = ""
    http_methods: list[str] = field(default_factory=lambda: ["GET"])

class PermissionRegistry:
    """Singleton registry for tracking view permissions."""

    _instance: ClassVar["PermissionRegistry | None"] = None
    _views: dict[str, ViewPermission]

    def __new__(cls) -> "PermissionRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._views = {}
        return cls._instance

    def register(
        self,
        view_func,
        requires_login: bool = False,
        permissions: list[str] | None = None,
        inline_permissions: list[str] | None = None,
    ) -> None:
        """Register a view's permission requirements."""
        ...

    def get_all(self) -> list[ViewPermission]:
        """Return all registered views sorted by URL."""
        ...

    def get_by_app(self) -> dict[str, list[ViewPermission]]:
        """Return views grouped by app name."""
        ...

# Global singleton
registry = PermissionRegistry()
```

### Step 2: Update Decorators to Auto-Register

Modify `apps/accounts/decorators.py` to register views when decorators are applied:

```python
from apps.accounts.permission_registry import registry

def discord_permission_required(permission, ...):
    perms = (permission,) if isinstance(permission, str) else tuple(permission)

    def decorator(view_func):
        # Register this view's permissions
        registry.register(
            view_func,
            requires_login=True,  # This decorator implies login required
            permissions=list(perms),
        )

        # ... existing wrapper logic ...
        return wrapped

    return decorator
```

Also create a tracking wrapper for `@login_required`:

```python
def tracked_login_required(view_func):
    """Login required decorator that registers to permission registry."""
    registry.register(view_func, requires_login=True)
    return login_required(view_func)
```

### Step 3: Handle Inline Permission Checks

For views that check permissions in the view body (not via decorator), add a decorator to document them:

```python
def inline_permission(permissions: list[str], description: str = ""):
    """Document inline permission checks in view body.

    This decorator doesn't enforce permissions - it only documents
    that the view performs additional permission checks internally.
    """
    def decorator(view_func):
        registry.register(
            view_func,
            inline_permissions=permissions,
        )
        return view_func
    return decorator
```

Usage in views:

```python
@login_required
@team_member_required()
@inline_permission(["link_admin"], "Only link admins can create links")
def submit_team_link_view(request):
    # Existing inline check
    if not request.user.is_link_admin and not request.user.is_superuser:
        ...
```

### Step 4: Create URL Introspection Helper

Create a management command or utility to extract URL patterns and match them to registered views:

```python
# apps/accounts/utils.py (add to existing)

from django.urls import get_resolver, URLPattern, URLResolver

def get_all_url_patterns(urlpatterns=None, prefix=""):
    """Recursively extract all URL patterns from the project."""
    if urlpatterns is None:
        urlpatterns = get_resolver().url_patterns

    patterns = []
    for pattern in urlpatterns:
        if isinstance(pattern, URLResolver):
            # Recurse into included urlconfs
            new_prefix = prefix + str(pattern.pattern)
            patterns.extend(get_all_url_patterns(pattern.url_patterns, new_prefix))
        elif isinstance(pattern, URLPattern):
            patterns.append({
                "url": prefix + str(pattern.pattern),
                "name": pattern.name,
                "callback": pattern.callback,
            })
    return patterns
```

### Step 5: Create the Site Map View

Add to `apps/accounts/views.py`:

```python
@login_required
@discord_permission_required("app_admin", raise_exception=True)
def permissions_sitemap(request: HttpRequest) -> HttpResponse:
    """Display all views and their permission requirements."""
    from apps.accounts.permission_registry import registry
    from apps.accounts.utils import get_all_url_patterns

    # Get registered permissions
    views_by_app = registry.get_by_app()

    # Get all URL patterns for cross-reference
    all_urls = get_all_url_patterns()

    return render(
        request,
        "accounts/permissions_sitemap.html",
        {
            "views_by_app": views_by_app,
            "all_urls": all_urls,
        },
    )
```

Add URL pattern:

```python
# apps/accounts/urls.py
path("permissions/", views.permissions_sitemap, name="permissions_sitemap"),
```

### Step 6: Create Template

Create `theme/templates/accounts/permissions_sitemap.html`:

```html
{% extends "base.html" %}
{% block title %}Permissions Site Map{% endblock %}

{% block content %}
<div class="container mx-auto p-4">
    <h1 class="text-2xl font-bold mb-6">Permissions Site Map</h1>

    <!-- Legend -->
    <div class="card bg-base-200 mb-6">
        <div class="card-body">
            <h2 class="card-title">Legend</h2>
            <div class="flex flex-wrap gap-4">
                <span class="badge badge-ghost">Public</span>
                <span class="badge badge-info">Login Required</span>
                <span class="badge badge-warning">Team Member</span>
                <span class="badge badge-error">Admin/Captain</span>
            </div>
        </div>
    </div>

    <!-- Views by App -->
    {% for app_name, views in views_by_app.items %}
    <div class="card bg-base-100 shadow mb-4">
        <div class="card-body">
            <h2 class="card-title">{{ app_name }}</h2>
            <div class="overflow-x-auto">
                <table class="table table-zebra">
                    <thead>
                        <tr>
                            <th>URL</th>
                            <th>View Name</th>
                            <th>Login</th>
                            <th>Permissions</th>
                            <th>Methods</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for view in views %}
                        <tr>
                            <td><code>{{ view.url_pattern }}</code></td>
                            <td>{{ view.view_name }}</td>
                            <td>
                                {% if view.requires_login %}
                                <span class="badge badge-info badge-sm">Yes</span>
                                {% else %}
                                <span class="badge badge-ghost badge-sm">No</span>
                                {% endif %}
                            </td>
                            <td>
                                {% for perm in view.permissions %}
                                <span class="badge badge-warning badge-sm">{{ perm }}</span>
                                {% endfor %}
                                {% for perm in view.inline_permissions %}
                                <span class="badge badge-error badge-sm" title="Checked in view">{{ perm }}*</span>
                                {% endfor %}
                            </td>
                            <td>{{ view.http_methods|join:", " }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    {% endfor %}
</div>
{% endblock %}
```

### Step 7: Add Navigation Link

Add a link to the permissions sitemap in the admin section of the header for users with `app_admin` permission.

---

## Alternative: Static Configuration Approach

If automatic registration proves too complex, a simpler alternative is to maintain a static configuration file:

Create `apps/accounts/permissions_config.py`:

```python
PERMISSIONS_MAP = [
    {
        "app": "core",
        "views": [
            {"url": "/", "name": "home", "login": False, "permissions": []},
            {"url": "/about/", "name": "about", "login": False, "permissions": []},
        ]
    },
    {
        "app": "accounts",
        "views": [
            {"url": "/user/profile/", "name": "profile", "login": True, "permissions": []},
            # ... more views
        ]
    },
    # ... more apps
]
```

This is simpler but requires manual updates when views change.

---

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `apps/accounts/permission_registry.py` | Create | Permission registry singleton |
| `apps/accounts/decorators.py` | Modify | Add registry calls to decorators |
| `apps/accounts/utils.py` | Modify | Add URL introspection helper |
| `apps/accounts/views.py` | Modify | Add `permissions_sitemap` view |
| `apps/accounts/urls.py` | Modify | Add URL for sitemap view |
| `theme/templates/accounts/permissions_sitemap.html` | Create | Template for the sitemap |
| `apps/team/views.py` | Modify | Add `@inline_permission` decorators |
| `theme/templates/header.html` | Modify | Add nav link for admins |

---

## Testing Plan

1. Verify all existing views continue to work after decorator changes
2. Verify the registry correctly captures all decorated views
3. Verify URL introspection returns all routes
4. Verify the sitemap page displays correctly
5. Verify only `app_admin` users can access the sitemap

---

## Future Enhancements

1. **Export to JSON/CSV**: Add ability to export the permissions map
2. **Audit logging**: Track when permissions are checked/denied
3. **Comparison tool**: Compare current permissions vs. a baseline
4. **API endpoint**: Expose permissions map via the dbot API for the Discord bot to use