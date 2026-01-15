# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django 6.0 application for The Coalition Zwift racing team. Integrates with ZwiftPower and Zwift Racing APIs to manage
team data and member information.

## Commands

```bash
# Package management (uses uv)
uv sync                          # Install dependencies
uv add <package>                 # Add production dependency
uv add --dev <package>           # Add dev dependency

# Django
uv run python manage.py runserver              # Dev server
uv run python manage.py check                  # Validate config
uv run python manage.py makemigrations         # Create migrations
uv run python manage.py migrate                # Apply migrations
uv run python manage.py createsuperuser        # Create admin user

# Background Tasks (Django 6.0 built-in)
uv run python manage.py db_worker              # Run task worker

# Tailwind CSS
uv run python manage.py tailwind install       # Install npm deps
uv run python manage.py tailwind start         # Dev mode with watch
uv run python manage.py tailwind build         # Production build

# Testing & Linting
uv run pytest                                  # Run tests
uv run pytest <path>::<test>                   # Run single test
uv run ruff check .                            # Lint
uv run ruff check . --fix                      # Lint and fix
uv run ruff format .                           # Format code

# Production (uses Granian WSGI server)
uv run granian gotta_bike_platform.wsgi:application --interface wsgi
```

## Architecture

### Configuration

- `gotta_bike_platform/config.py` - pydantic-settings for environment variables (loaded from `.env`)
- `gotta_bike_platform/settings.py` - Django settings, imports config values from `config.py`
- Optional env vars: `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` (OAuth)
- Optional env vars: `LOGFIRE_TOKEN`, `LOGFIRE_ENVIRONMENT` (observability)
- Runtime settings (via constance): API credentials and team settings (see Dynamic Settings below)

### Static Files & Storage

Static files and media (user uploads) are handled separately for optimal performance:

- **Static files** (CSS, JS, images): Served via [WhiteNoise](https://whitenoise.readthedocs.io/) directly from the app
  server. `collectstatic` writes to local `staticfiles/` directory (fast deploys, no S3 upload).
- **Media files** (user uploads): Stored on S3-compatible storage when configured (Railway object storage).

Configuration in `settings.py`:

```python
# Static files: Always WhiteNoise (fast, compressed, cached)
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Media files: S3 when configured, otherwise local filesystem
if config.use_s3_storage:
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {...},
    }
```

Environment variables for S3 (optional):

- `AWS_S3_ENDPOINT_URL` - S3 endpoint (e.g., `https://storage.railway.app`)
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` - S3 credentials
- `AWS_STORAGE_BUCKET_NAME` - Bucket name
- `AWS_S3_REGION_NAME` - Region (default: `auto`)

WhiteNoise features:

- Automatic gzip/brotli compression
- Far-future cache headers (hashed filenames)
- Serves files directly from memory in production

### Apps (in `apps/`)

- `accounts` - Custom User model with Discord/Zwift fields, django-allauth adapters, role-based permissions
    - `GuildMember` model - Tracks Discord guild members synced from bot (see Guild Member Sync below)
    - `decorators.py` - `discord_permission_required` decorator for view permissions
- `team` - Core team management:
    - `RaceReadyRecord` - Verification records for weight/height/power (pending/verified/rejected status)
    - `TeamLink` - Links to external resources with visibility date ranges
    - `RosterFilter` - Temporary filtered roster views from Discord channel members (5-min expiration)
    - `services.py` - `get_unified_team_roster()` merges data from ZwiftPower, Zwift Racing, and User accounts;
      `get_user_verification_types(user)` returns allowed verification types based on ZP category
- `zwift` - Zwift integration (placeholder)
- `zwiftpower` - ZwiftPower API integration:
    - `ZPTeamRiders` - Team roster data from admin API
    - `ZPEvent` - Event metadata
    - `ZPRiderResults` - Individual rider results per event
- `zwiftracing` - Zwift Racing API integration, `ZRRider` model for rider data
- `dbot_api` - Discord bot REST API using Django Ninja
- `data_connection` - Google Sheets data export:
    - `DataConnection` model - Configurable exports to Google Sheets
    - `gs_client.py` - Google Sheets/Drive API client using service account
    - Supports field selection from User, ZwiftPower, and Zwift Racing data
    - Configurable filters (gender, division, rating, phenotype)
    - Manual sync clears sheet and rewrites all data
- `magic_links` - Passwordless authentication (legacy)

### Authentication (django-allauth)

- Discord OAuth only (no username/password)
- **Guild membership required**: Users must be a member of the configured Discord server (`GUILD_ID`) to sign up or log
  in
- Custom User model fields: `discord_id`, `discord_username`, `discord_nickname`, `zwid`
- TOTP two-factor authentication via `allauth.mfa`
- Custom adapter (`apps/accounts/adapters.py`):
    - Verifies guild membership via Discord API before allowing login/signup
    - Syncs Discord profile data on login
    - Redirects rejected users to `DISCORD_URL` (invite link)
- OAuth scopes: `identify`, `email`, `guilds`
- URLs at `/accounts/` (login, logout, 2fa management)

#### Discord OAuth Adapter (`apps/accounts/adapters.py`)

The custom `DiscordSocialAccountAdapter` handles Discord OAuth login with careful data preservation:

**Key Methods:**

1. **`pre_social_login`** - Called on every login attempt
   - Checks guild membership before allowing access
   - **Critical**: Reconnects existing users by `discord_id` if their SocialAccount was lost
   - Only updates Discord-specific fields (`discord_id`, `discord_username`, `discord_nickname`, `discord_avatar`)
   - **Never** overwrites profile fields (`first_name`, `last_name`, `birth_year`, `gender`, `timezone`, `country`)

2. **`populate_user`** - Called only for NEW users
   - Sets Discord fields and email from OAuth data
   - Leaves profile fields empty (user must complete profile manually)

3. **`save_user`** - Called only for NEW users
   - Creates User and SocialAccount records
   - Syncs Discord roles

**User Reconnection Logic:**

If allauth can't find a SocialAccount (e.g., it was deleted), the adapter checks if a User with matching `discord_id`
exists and reconnects them:

```python
# In pre_social_login:
if not sociallogin.is_existing and discord_id:
    try:
        existing_user = User.objects.get(discord_id=discord_id)
        sociallogin.connect(request, existing_user)
        sociallogin.is_existing = True
    except User.DoesNotExist:
        pass  # Truly a new user
```

This prevents profile data loss when SocialAccount records are missing.

**Important:** When modifying OAuth adapter code, always use `update_fields` when saving User objects to prevent
accidentally overwriting profile data:

```python
# Correct - only updates specified fields
user.save(update_fields=['discord_id', 'discord_username', 'discord_nickname', 'discord_avatar'])

# Wrong - overwrites entire model including profile fields
user.save()
```

### Profile Completion

Users are encouraged to complete their profile but are **not blocked** from accessing the app.

#### Profile Fields

- `first_name`, `last_name` - User's real name
- `birth_year` - Year of birth (validated: 1900 to current_year - 13)
- `gender` - Gender (male/female/other)
- `timezone` - User's timezone (e.g., "America/New_York")
- `country` - Country of residence (uses `django-countries` CountryField with ISO 2-letter codes, rendered as dropdown)
- `zwid_verified` - Zwift account verification status

#### User Model Properties

```python
# Check if profile is complete
if user.is_profile_complete:
    # All required fields filled AND Zwift verified
    ...

# Get detailed status for UI
status = user.profile_completion_status
# Returns: {"first_name": True, "last_name": False, "birth_year": True, ...}
```

#### Warning Banner

Instead of blocking access, a red warning banner is displayed at the top of every page for users with incomplete
profiles. The banner is defined in `theme/templates/base.html` and links to the profile edit page.

### Discord Role-Based Permissions (`apps/accounts/models.py`)

Permissions are granted via Discord roles configured in Constance. The system checks permissions in this order:

1. **Superusers** always have all permissions
2. **Manual overrides** in `User.permission_overrides` (explicit grant/revoke)
3. **Discord roles** matched against Constance permission settings
4. **Legacy app roles** in `User.roles` (backward compatibility)

#### Available Permissions

- `app_admin` - Full application admin
- `team_captain` - Team captain role
- `vice_captain` - Vice captain role
- `link_admin` - Can create, edit and delete team links
- `membership_admin` - Membership management
- `racing_admin` - Racing management
- `team_member` - Required for most pages; without it users can only see index and their profile
- `race_ready` - Race ready status
- `approve_verification` - Can approve/reject verification records

#### Constance Permission Settings

Configure in Django admin at `/admin/constance/config/` under "Permission Mappings":

- `PERM_APP_ADMIN_ROLES` - JSON array of Discord role IDs, e.g., `["1234567890123456789"]`
- `PERM_TEAM_CAPTAIN_ROLES`, `PERM_VICE_CAPTAIN_ROLES`, `PERM_LINK_ADMIN_ROLES`, etc.
- `PERM_APPROVE_VERIFICATION_ROLES` - Role IDs that can approve/reject verification records

#### Usage in Views

```python
# Using decorator
from apps.accounts.decorators import discord_permission_required

@login_required
@discord_permission_required("team_captain", raise_exception=True)
def verify_record(request):
    ...

# Multiple permissions (OR logic - user needs ANY)
@discord_permission_required(["team_captain", "vice_captain"])
def view_records(request):
    ...

# Using User methods directly
if request.user.has_permission("team_captain"):
    ...

# Property shortcuts still work
if request.user.is_team_captain:
    ...
```

**Note**: The `discord_permission_required` decorator raises `PermissionDenied` (403) for authenticated users who lack
permission, rather than redirecting to login. This prevents redirect loops. A custom `templates/403.html` provides a
user-friendly error page.

#### Manual Permission Overrides

Set in Django admin User edit page under "Permissions" fieldset:

```json
{"team_captain": true}   // Grant without Discord role
{"team_captain": false}  // Revoke despite Discord role
```

#### Keeping Roles in Sync

Discord roles are synced via `/api/dbot/sync_user_roles/{discord_id}` endpoint called by the Discord bot.
User's `discord_roles` field stores `{role_id: role_name}` mapping from Discord.

### Background Tasks

Uses Django 6.0's built-in `django-tasks` with database backend. Tasks are defined with `@task` decorator:

```python
from django.tasks import task

@task
def my_task() -> dict:
    ...
    return {"status": "complete"}

# Enqueue immediately
my_task.enqueue()

# Enqueue with delay (run_after must be datetime, not timedelta)
from django.utils import timezone
from datetime import timedelta
my_task.using(run_after=timezone.now() + timedelta(seconds=60)).enqueue()
```

### External API Clients

- `apps/zwiftpower/zp_client.py` - ZwiftPower session-based client using httpx (requires Zwift OAuth login)
- `apps/zwiftracing/zr_client.py` - Zwift Racing API client using httpx
    - All methods return `(status_code, response_json)` tuple
    - 429 rate limit errors return the response without raising (contains `retryAfter` seconds)
    - Non-success status codes (except 429) raise `httpx.HTTPStatusError`

### Discord Bot API (`apps/dbot_api`)

REST API using Django Ninja for Discord bot integration:

- Auth: `X-API-Key` header (matches constance `DBOT_AUTH_KEY`) + `X-Guild-Id` header (must match constance `GUILD_ID`) +
  `X-Discord-User-Id` header
- Key endpoints:
    - `GET /api/dbot/zwiftpower_profile/{zwid}` - ZwiftPower rider data
    - `GET /api/dbot/my_profile` - Combined profile for requesting Discord user
    - `GET /api/dbot/teammate_profile/{zwid}` - Combined profile for any teammate
    - `POST /api/dbot/sync_guild_roles` - Sync all Discord roles
    - `POST /api/dbot/sync_guild_members` - Sync all guild members (see Guild Member Sync)
    - `POST /api/dbot/sync_user_roles/{discord_id}` - Sync a user's roles
    - `POST /api/dbot/roster_filter` - Create filtered roster link from Discord channel members

### Cron API (`apps/dbot_api/cron_api.py`)

REST API for triggering scheduled tasks via external cron service:

- Auth: `X-Cron-Key` header (uses same `DBOT_AUTH_KEY` from constance)
- Endpoints:
    - `GET /api/cron/tasks` - List available tasks
    - `POST /api/cron/task/{task_name}` - Trigger a task by name
- Available tasks:
    - `update_team_riders` - Fetch team riders from ZwiftPower
    - `update_team_results` - Fetch team results from ZwiftPower
    - `sync_zr_riders` - Sync riders from Zwift Racing API

To add new tasks, update `TASK_REGISTRY` in `cron_api.py`:

```python
TASK_REGISTRY: dict = {
    "task_name": {
        "task": task_function,
        "description": "What the task does",
    },
}
```

Example cron call:

```bash
curl -X POST -H "X-Cron-Key: your-key" https://domain.com/api/cron/task/update_team_riders
```

### URL Routes (`gotta_bike_platform/urls.py`)

- `/` - Home page
- `/about/` - About page
- `/admin/` - Django admin
- `/accounts/` - allauth (login, logout, MFA)
- `/user/` - User profile and settings (`apps.accounts.urls`)
- `/team/` - Team management (`apps.team.urls`)
- `/site/config/` - Site configuration (Constance settings UI)
- `/data-connections/` - Google Sheets exports (`apps.data_connection.urls`)
- `/api/dbot/` - Discord bot API
- `/api/cron/` - Cron task API
- `/m/` - Magic links (legacy)

### Frontend

- `theme/` - django-tailwind app with Tailwind CSS 4.x + DaisyUI 5.x
- `theme/templates/` - Base templates (base.html, header.html, footer.html)
    - `base.html` includes site announcement banner (yellow) when `config.SITE_ANNOUNCEMENT` is set (supports Markdown)
    - `base.html` includes profile incomplete warning (red) for users with incomplete profiles
    - `header.html` displays logo/team name based on `LOGO_DISPLAY_MODE` setting (see Site Image Settings)
- `templates/index.html` - Home page with hero section (supports background image via `site_settings.hero_image`)
- `templates/account/` - Auth templates (login, logout) with DaisyUI styling
- `templates/mfa/` - MFA templates (TOTP setup, recovery codes)
- Uses HTMX for interactivity (`django-htmx` middleware enabled)

### Admin Customization

Custom admin buttons are added via:

1. Override `get_urls()` to add custom URL path
2. Add view method that enqueues task and redirects
3. Create template extending `admin/change_list.html` with button in `object-tools-items` block

### Dynamic Settings (django-constance)

Runtime-configurable settings stored in database, editable via Django admin at `/admin/constance/config/`.

Available settings:

- **Team Identity**: `GUILD_NAME`, `GUILD_ID`, `DISCORD_URL` (invite link for rejected users)
- **Zwift Credentials**: `ZWIFT_USERNAME`, `ZWIFT_PASSWORD` (password fields), `ZWIFTPOWER_TEAM_ID`
- **API Keys**: `DBOT_AUTH_KEY` (password field - masked in admin)
- **Zwift Racing App**: `ZRAPP_API_URL`, `ZRAPP_API_KEY` (password field - masked in admin)
- **Permission Mappings**: `PERM_APP_ADMIN_ROLES`, `PERM_TEAM_CAPTAIN_ROLES`, `PERM_VICE_CAPTAIN_ROLES`,
  `PERM_LINK_ADMIN_ROLES`, `PERM_MEMBERSHIP_ADMIN_ROLES`, `PERM_RACING_ADMIN_ROLES`, `PERM_TEAM_MEMBER_ROLES`,
  `PERM_RACE_READY_ROLES` (JSON arrays of Discord role IDs)
- **Discord Roles**: `RACE_READY_ROLE_ID` (Discord role ID assigned when user is race ready, `0` to disable)
- **Verification**: `CATEGORY_REQUIREMENTS` (JSON mapping ZP divisions to required verification types),
  `WEIGHT_FULL_DAYS` (180), `WEIGHT_LIGHT_DAYS` (30), `HEIGHT_VERIFICATION_DAYS` (0=forever),
  `POWER_VERIFICATION_DAYS` (365)
- **Google Settings**: `GOOGLE_SERVICE_ACCOUNT_EMAIL`, `GOOGLE_DRIVE_FOLDER_ID` (shared folder for spreadsheets)
- **Site Settings**: `TEAM_NAME`, `SUBTITLE`, `SITE_ANNOUNCEMENT` (yellow banner at top of all pages, supports Markdown),
  `MAINTENANCE_MODE`, `LOGO_DISPLAY_MODE` (header display: `logo_only`, `logo_and_name`, or `name_only`)

Usage in code:

```python
from constance import config

team_id = config.ZWIFTPOWER_TEAM_ID
guild_id = config.GUILD_ID
if config.MAINTENANCE_MODE:
    ...
```

Add new settings in `settings.py` under `CONSTANCE_CONFIG`.

### Site Image Settings

Site images (logo and hero image) are stored in the `SiteSettings` singleton model (`gotta_bike_platform/models.py`),
separate from Constance because they require file uploads.

**SiteSettings Model:**

```python
from gotta_bike_platform.models import SiteSettings

settings = SiteSettings.get_settings()  # Returns singleton instance
settings.site_logo    # ImageField - Logo displayed in header
settings.favicon      # ImageField - Favicon for browser tabs
settings.hero_image   # ImageField - Background image for home page hero
```

**Context Processor:**

The `site_settings` context processor (`gotta_bike_platform/context_processors.py`) makes the settings available in all
templates as `site_settings`:

```html
{% if site_settings.site_logo %}
<img src="{{ site_settings.site_logo.url }}" alt="Logo">
{% endif %}
```

**Logo Display Mode:**

The `LOGO_DISPLAY_MODE` Constance setting controls how the header displays the team identity:

| Value           | Behavior                                    |
|-----------------|---------------------------------------------|
| `name_only`     | Show only `TEAM_NAME` text (default)        |
| `logo_only`     | Show only the uploaded logo                 |
| `logo_and_name` | Show both logo and team name                |

If `logo_only` or `logo_and_name` is set but no logo is uploaded, the team name is shown as fallback.

**Hero Image:**

When a hero image is uploaded, the home page (`templates/index.html`) displays it as a background with a dark overlay
for text readability. Without a hero image, the section uses a solid `bg-base-200` background.

**Configuration UI:**

Site images can be managed at `/site/config/` in the "Site Images" section. The form supports:
- Uploading new logo/hero images
- Deleting existing images (red X button)
- Preview of current images

**Files:**

- `gotta_bike_platform/models.py` - `SiteSettings` singleton model
- `gotta_bike_platform/context_processors.py` - Makes `site_settings` available in templates
- `gotta_bike_platform/admin.py` - Admin registration
- `apps/accounts/views.py` - `config_site_images_update` view
- `templates/accounts/partials/config_site_images.html` - Upload form partial

## Code Style

Ruff configuration in `ruff.toml`:

- Python 3.14 target
- Line length: 120
- Enforces: Django (DJ), security (S), docstrings (D), isort (I), bugbear (B), and more
- Docstrings required (Google style, D212 format)

New Django apps should be created in `apps/` with config name `apps.<appname>`.

When handling API responses that may contain `None` values for string fields, use `value or ""` pattern (not
`.get("key", "")` which returns `None` if key exists with `None` value).

## Observability (Logfire)

The application uses [Logfire](https://logfire.pydantic.dev/) for observability, logging, and monitoring.

### Configuration

Logfire is configured at the top of `settings.py`:

```python
import logfire

logfire.configure(
    service_name="coalition-platform",
    environment=config.logfire_environment,  # from .env
    token=config.logfire_token,              # from .env (optional)
    send_to_logfire="if-token-present",
)
```

Instrumentation is added at the **end** of `settings.py` (after Django is fully configured):

```python
logfire.instrument_django()
logfire.instrument_httpx()
logfire.info("Django settings loaded", environment=config.logfire_environment)
```

### Environment Variables

- `LOGFIRE_TOKEN` - Logfire API token (optional; if not set, logs are local only)
- `LOGFIRE_ENVIRONMENT` - Environment name (e.g., "production", "development")

### Usage in Code

```python
import logfire

# Structured logging
logfire.info("User logged in", user_id=user.id, discord_id=user.discord_id)
logfire.warning("Rate limit approaching", api="zwiftpower", remaining=5)
logfire.error("API request failed", error=str(e), endpoint=url)
```

## Guild Member Sync

Syncs Discord guild members with Django to track membership status.

### GuildMember Model (`apps/accounts/models.py`)

Stores Discord guild member data:

- `discord_id` - Discord user ID (unique)
- `username`, `display_name`, `nickname` - Discord names
- `avatar_hash`, `roles`, `joined_at`, `is_bot` - Discord data
- `date_created`, `date_modified`, `date_left` - Tracking timestamps
- `user` - OneToOne link to User (if they have an account)

### How It Works

1. Discord bot collects all guild members via `/sync_members` slash command
2. Bot POSTs member data to `POST /api/dbot/sync_guild_members`
3. Django creates/updates GuildMember records
4. Members not in payload are marked as left (`date_left` set)
5. GuildMembers are linked to User accounts by matching `discord_id`

**Important**: Only affects Discord OAuth users. Regular Django accounts (staff, admin) without `discord_id` are NOT
modified.

### Admin Views

- Guild Members list: `/admin/accounts/guildmember/`
- Comparison view: `/admin/accounts/guildmember/comparison/`
    - Guild Only: Discord members without User accounts
    - Linked: Discord members with User accounts
    - Left Guild: Members who left but have User accounts
    - Discord Users (No Guild): OAuth users without GuildMember record

### Discord Bot Requirements

The bot needs the **Server Members Intent** (privileged):

1. Enable `intents.members = True` in bot code (`src/bot.py`)
2. Enable "Server Members Intent" in Discord Developer Portal > Bot > Privileged Gateway Intents

## Race Ready Verification

Users can achieve "Race Ready" status by completing verification requirements. This status gates participation in
official team races.

### Requirements

A user is race ready (`User.is_race_ready` property) when they have **ALL** verification types required for their
ZwiftPower category. The property dynamically checks requirements based on `CATEGORY_REQUIREMENTS`.

### Category-Based Verification Types

The verification types **required** for race ready depend on the user's ZwiftPower category (`div` for male, `divw` for
female). Configured via `CATEGORY_REQUIREMENTS` Constance setting:

```json
{"5": ["weight_full", "height", "power"], "10": ["weight_full", "height"], ...}
```

| ZP Div | Category | Required Types                 |
|--------|----------|--------------------------------|
| 5      | A+       | weight_full, height, power     |
| 10-30  | A-C      | weight_full, height            |
| 40-50  | D-E      | weight_light, height           |
| (none) | -        | weight_light, height (default) |

The `get_user_verification_types(user)` function in `apps/team/services.py` returns required types for a user. This is
used by both the `is_race_ready` property and the verification submission form.

### Verification Flow

1. User submits a `RaceReadyRecord` (weight, height, or power photo) via the web app
2. Record starts in `pending` status
3. Users with `approve_verification` permission review and verify/reject records
4. Verified records expire based on Constance settings:
   - `WEIGHT_FULL_DAYS` (default: 120 days)
   - `HEIGHT_VERIFICATION_DAYS` (default: 0 = never expires)
   - `POWER_VERIFICATION_DAYS` (default: 365 days)

### Race Ready Role Assignment

When a user's `is_race_ready` status is True, the Discord bot automatically assigns them the Race Ready Discord role.
This happens when users use:

- `/my_profile` - User checks their own profile
- `/sync_my_roles` - User manually syncs their roles

**Constance Settings:**

- `RACE_READY_ROLE_ID` - Discord role ID to assign (set to `0` to disable)

**API Response:**

The `/my_profile` and `/sync_user_roles` endpoints return:

```json
{
  "is_race_ready": true,
  "race_ready_role_id": "1234567890123456789"
}
```

The Discord bot uses these fields to add/remove the role based on current verification status.

### Team Roster

The team roster view (`/team/roster/`) displays race ready status with a filter option. The `data_connection` module
also supports exporting `race_ready` status to Google Sheets.
