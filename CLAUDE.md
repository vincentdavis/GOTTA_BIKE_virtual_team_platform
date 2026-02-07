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

- **Static files**: WhiteNoise (compressed, cached, served from memory). `collectstatic` writes to `staticfiles/`.
- **Media files**: S3-compatible storage when configured (Railway), otherwise local filesystem.
- S3 env vars (optional): `AWS_S3_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_REGION_NAME`

### Apps (in `apps/`)

- `accounts` - Custom User model with Discord/Zwift fields, django-allauth adapters, role-based permissions
    - `GuildMember` model - Tracks Discord guild members synced from bot (see Guild Member Sync below)
    - `YouTubeVideo` model - Stores YouTube videos fetched from team members' channels (via RSS), displayed on Team Feed
    - `decorators.py` - `discord_permission_required` decorator for view permissions
- `team` - Core team management:
    - `RaceReadyRecord` - Verification records for weight/height/power (pending/verified/rejected status)
    - `TeamLink` - Links to external resources with visibility date ranges
    - `RosterFilter` - Temporary filtered roster views from Discord channel members (5-min expiration)
    - `MembershipApplication` - New member registrations submitted via Discord modal (see Membership Registration below)
    - `DiscordRole` - Discord roles synced from server for permission checking
    - `services.py` - `get_unified_team_roster()` merges data from ZwiftPower, Zwift Racing, and User accounts;
      `get_user_verification_types(user)` returns allowed verification types based on ZP category
- `zwift` - Zwift integration (placeholder)
- `zwiftpower` - ZwiftPower API integration:
    - `ZPTeamRiders` - Team roster data from admin API
    - `ZPEvent` - Event metadata
    - `ZPRiderResults` - Individual rider results per event
- `zwiftracing` - Zwift Racing API integration:
    - `ZRRider` model - Rider data with seed/velo rating fields (`seed_race`, `seed_time_trial`, `seed_endurance`,
      `seed_pursuit`, `seed_sprint`, `seed_punch`, `seed_climb`, `seed_tt_factor` and matching `velo_*` fields)
- `analytics` - Server-side page visit tracking with client-side enrichment:
    - `PageVisit` model - Tracks page visits (user, IP, user agent, screen dimensions, browser/OS info)
    - `POST /api/analytics/track/` - Client-side tracking endpoint (Django Ninja)
    - Dashboard at `/analytics/` with period filters (day/week/month/year), requires `app_admin` permission
    - Client-side JS snippet in `base.html` sends screen/browser data to tracking endpoint
- `club_strava` - Strava Club Activities integration:
    - `ClubActivity` model - Stores Strava club activity data with distance/time formatting properties
    - `strava_client.py` - OAuth token refresh, activity fetching, bulk sync with rate limit handling
    - Views at `/strava/` - Activity list with sport_type and search filters
    - `sync_strava_activities()` background task for scheduled syncing
- `dbot_api` - Discord bot REST API using Django Ninja
- `data_connection` - Google Sheets data export:
    - `DataConnection` model - Configurable exports to Google Sheets
    - `gs_client.py` - Google Sheets/Drive API client using service account
    - Supports field selection from User, ZwiftPower, and Zwift Racing data
    - Configurable filters (gender, division, rating, phenotype)
    - Manual sync clears sheet and rewrites all data
- `magic_links` - Passwordless authentication (legacy)
- `cms` - Dynamic CMS pages:
    - `Page` model - Content pages with markdown, hero sections, and card layouts
    - Publishing workflow (draft/published status)
    - Navigation settings (show_in_nav, nav_order, nav_title)
    - Access control (require_login, require_team_member)

### Authentication (django-allauth)

- Discord OAuth only (no username/password)
- **Guild membership required**: Users must be a member of the configured Discord server (`GUILD_ID`) to sign up or log
  in
- Custom User model fields: `discord_id`, `discord_username`, `discord_nickname`, `zwid`,
  social fields (`strava_url`, `youtube_channel`, `youtube_channel_id`, `twitch_channel`, `instagram_url`,
  `facebook_url`, `twitter_url`, `tiktok_url`, `bluesky_url`, `mastodon_url`, `garmin_url`, `tpv_profile_url`),
  equipment fields (`trainer`, `powermeter`, `dual_recording`, `heartrate_monitor`)
- TOTP two-factor authentication via `allauth.mfa`
- Custom adapter (`apps/accounts/adapters.py`):
    - Verifies guild membership via Discord API before allowing login/signup
    - Syncs Discord profile data on login
    - Redirects rejected users to `DISCORD_URL` (invite link)
- OAuth scopes: `identify`, `email`, `guilds`
- URLs at `/accounts/` (login, logout, 2fa management)

#### Discord OAuth Adapter (`apps/accounts/adapters.py`)

**Critical gotchas:**

- `pre_social_login` reconnects existing users by `discord_id` if SocialAccount was lost — prevents profile data loss
- `pre_social_login` only updates Discord fields, **never** profile fields (`first_name`, `last_name`, `birth_year`, etc.)
- `populate_user` and `save_user` are only called for NEW users
- **Always use `update_fields`** when saving User in adapter code: `user.save(update_fields=['discord_id', ...])` — bare `user.save()` overwrites profile data

### Profile Completion

Users are encouraged to complete their profile but are **not blocked** from accessing the app.

#### Profile Fields

Required fields for profile completion:

- `first_name`, `last_name` - User's real name
- `birth_year` - Year of birth (validated: 1900 to current_year - 13)
- `gender` - Gender (male/female/other)
- `timezone` - User's timezone (e.g., "America/New_York")
- `country` - Country of residence (uses `django-countries` CountryField with ISO 2-letter codes, rendered as dropdown)
- `trainer` - Smart trainer type (required for racing)
- `heartrate_monitor` - Heart rate monitor type (required for racing)
- `zwid_verified` - Zwift account verification status

Properties: `user.is_profile_complete` (bool), `user.profile_completion_status` (dict of field→bool).
Incomplete profiles show a red warning banner in `base.html` (not blocking, just a warning).

### Public User Profiles

Public profiles at `/user/profile/<user_id>/` (requires `team_member` permission). Viewing own profile redirects to private page.

**Privacy**: Never expose `birth_year`, `email`, or emergency contact fields on public profiles. See `public_profile_view` in `apps/accounts/views.py`.

User names link to public profiles in roster and membership review tables.

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
- `performance_verification_team` - Performance verification team member
- `data_connection` - Access to Google Sheets data exports
- `pages_admin` - Can create and manage CMS pages

#### Constance Permission Settings

Configure in Django admin at `/admin/constance/config/` under "Permission Mappings":

- `PERM_APP_ADMIN_ROLES` - JSON array of Discord role IDs, e.g., `["1234567890123456789"]`
- `PERM_TEAM_CAPTAIN_ROLES`, `PERM_VICE_CAPTAIN_ROLES`, `PERM_LINK_ADMIN_ROLES`, etc.
- `PERM_APPROVE_VERIFICATION_ROLES` - Role IDs that can approve/reject verification records
- `PERM_PERFORMANCE_VERIFICATION_TEAM_ROLES` - Role IDs for performance verification team
- `PERM_DATA_CONNECTION_ROLES` - Role IDs that can access data exports
- `PERM_PAGES_ADMIN_ROLES` - Role IDs that can manage CMS pages

#### Usage in Views

- Decorator: `@discord_permission_required("team_captain", raise_exception=True)` — raises 403, not redirect (prevents loops)
- Multiple permissions (OR logic): `@discord_permission_required(["team_captain", "vice_captain"])`
- Direct check: `request.user.has_permission("team_captain")` or `request.user.is_team_captain`

#### Manual Permission Overrides

Set in Django admin User edit page under "Permissions" fieldset:

```json
{"team_captain": true}   // Grant without Discord role
{"team_captain": false}  // Revoke despite Discord role
```

#### Keeping Roles in Sync

Discord roles are synced via `/api/dbot/sync_user_roles/{discord_id}` endpoint called by the Discord bot.
User's `discord_roles` field stores `{role_id: role_name}` mapping from Discord.

#### Updating Permission Registry

When adding a view with `@discord_permission_required` or `@team_member_required`, also add it to `PERMISSION_REGISTRY` in `apps/accounts/permission_registry.py` (format: `"/path/ - Description"` in the `views` list). This powers the help icons on `/site/config/`.

### Background Tasks

Uses Django 6.0's built-in `django-tasks` with database backend. Define with `@task` decorator, enqueue with `.enqueue()`.
**Gotcha**: `run_after` must be a `datetime`, not `timedelta` — use `my_task.using(run_after=timezone.now() + timedelta(seconds=60)).enqueue()`.

### External API Clients

- `apps/zwiftpower/zp_client.py` - ZwiftPower session-based client using httpx (requires Zwift OAuth login)
- `apps/zwiftracing/zr_client.py` - Zwift Racing API client using httpx
    - All methods return `(status_code, response_json)` tuple
    - 429 rate limit errors return the response without raising (contains `retryAfter` seconds)
    - Non-success status codes (except 429) raise `httpx.HTTPStatusError`
- `apps/club_strava/strava_client.py` - Strava API client using httpx
    - `refresh_access_token()` - OAuth token refresh, auto-updates Constance config
    - `get_club_activities()` - Fetch activities with automatic token refresh on 401
    - `sync_club_activities()` - Bulk fetch and database sync with transaction support
    - Returns `(status_code, response)` tuple; handles 429 rate limits gracefully

### Discord Bot API (`apps/dbot_api`)

REST API using Django Ninja for Discord bot integration:

- Auth: `X-API-Key` header (matches constance `DBOT_AUTH_KEY`) + `X-Guild-Id` header (must match constance `GUILD_ID`) +
  `X-Discord-User-Id` header
- Key endpoints:
    - `GET /api/dbot/zwiftpower_profile/{zwid}` - ZwiftPower rider data
    - `GET /api/dbot/my_profile` - Combined profile for requesting Discord user
    - `GET /api/dbot/teammate_profile/{zwid}` - Combined profile for any teammate
    - `GET /api/dbot/search_teammates?q=` - Search teammates by name (for autocomplete)
    - `GET /api/dbot/team_links` - Get magic link to team links page
    - `POST /api/dbot/sync_guild_roles` - Sync all Discord roles
    - `POST /api/dbot/sync_guild_members` - Sync all guild members (see Guild Member Sync)
    - `POST /api/dbot/sync_user_roles/{discord_id}` - Sync a user's roles
    - `POST /api/dbot/roster_filter` - Create filtered roster link from Discord channel members
    - `POST /api/dbot/membership_application` - Create new membership registration from Discord
    - `GET /api/dbot/membership_application/{discord_id}` - Get membership registration by Discord ID
    - `POST /api/dbot/update_zp_team` - Trigger ZwiftPower team update task
    - `POST /api/dbot/update_zp_results` - Trigger ZwiftPower results update task

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
    - `guild_member_sync_status` - Report guild member sync health (actual sync done by Discord bot)

To add new cron tasks, update `TASK_REGISTRY` dict in `cron_api.py`.

### URL Routes (`gotta_bike_platform/urls.py`)

- `/` - Home page
- `/about/` - About page
- `/admin/` - Django admin
- `/accounts/` - allauth (login, logout, MFA)
- `/user/` - User profile and settings (`apps.accounts.urls`):
    - `/user/profile/` - User's own profile (private)
    - `/user/profile/edit/` - Edit profile
    - `/user/profile/<int:user_id>/` - Public profile (team members only)
- `/team/` - Team management (`apps.team.urls`):
    - `/team/roster/` - Team roster view
    - `/team/roster/f/{uuid}/` - Filtered roster view (temporary, 5-min expiration)
    - `/team/links/` - Team links
    - `/team/verification/` - Verification records (approvers only)
    - `/team/applications/` - Membership registrations list (admins only)
    - `/team/applications/{uuid}/` - Admin membership registration view
    - `/team/apply/{uuid}/` - Public membership registration form
    - `/team/performance-review/` - Performance review
    - `/team/discord-review/` - Discord guild member audit (membership admins only)
    - `/team/membership-review/` - Membership review (membership admins only)
    - `/team/team-feed/` - Team social media feed
- `/page/<slug>/` - CMS pages (`apps.cms.urls`)
- `/site/config/` - Site configuration (Constance settings UI)
- `/data-connections/` - Google Sheets exports (`apps.data_connection.urls`)
- `/strava/` - Strava club activities (`apps.club_strava.urls`)
- `/analytics/` - Analytics dashboard (`apps.analytics.urls`, requires `app_admin`)
- `/api/dbot/` - Discord bot API
- `/api/cron/` - Cron task API
- `/api/analytics/` - Analytics tracking API (`apps.analytics.api`)
- `/robots.txt` - Dynamic robots.txt (see Robots.txt section)
- `/m/` - Magic links (legacy)

### Frontend

- `theme/` - django-tailwind app with Tailwind CSS 4.x + DaisyUI 5.x
- `theme/templates/` - Base templates (base.html, sidebar.html, footer.html)
    - `base.html` includes site announcement banner (yellow) when `config.SITE_ANNOUNCEMENT` is set (supports Markdown)
    - `base.html` includes profile incomplete warning (red) for users with incomplete profiles
    - `base.html` has sticky header with logo/team name and user menu (avatar, dropdown)
    - `base.html` hides sidebar and hamburger menu for non-authenticated users (full-width layout)
    - `sidebar.html` contains navigation menu with conditional sections based on user permissions
- `templates/index.html` - Home page with hero section (supports background image via `site_settings.hero_image`)
- `templates/account/` - Auth templates (login, logout) with DaisyUI styling
- `templates/mfa/` - MFA templates (TOTP setup, recovery codes)
- Uses HTMX for interactivity (`django-htmx` middleware enabled)
- Google Analytics (GA4) tracking when `GOOGLE_ANALYTICS_ID` is configured (in `base.html`)
- Client-side analytics tracking JS sends page visit data to `/api/analytics/track/`

#### daisyUI Blueprint MCP

DaisyUI Blueprint MCP is configured. Use its tools (`generate_page`, `generate_section`, `generate_component`) when creating/modifying DaisyUI templates. Adapt generated code to Django template tags and follow patterns in `theme/templates/`.

### Admin Customization

Custom admin buttons are added via:

1. Override `get_urls()` to add custom URL path
2. Add view method that enqueues task and redirects
3. Create template extending `admin/change_list.html` with button in `object-tools-items` block

### Dynamic Settings (django-constance)

Runtime-configurable settings stored in database, editable via Django admin at `/admin/constance/config/`.

Settings are grouped: Team Identity, Zwift Credentials, API Keys, Permission Mappings (`PERM_*_ROLES` — JSON arrays of Discord role IDs), Discord Roles/Channels, New Arrival Messages (support `{member}`/`{server}` placeholders), Verification (`CATEGORY_REQUIREMENTS` JSON, `*_DAYS` expiration settings), Google, Strava, Site Settings.

Usage: `from constance import config; config.SETTING_NAME`. Add new settings in `settings.py` under `CONSTANCE_CONFIG`.

### Site Image Settings

`SiteSettings` singleton model (`gotta_bike_platform/models.py`) stores `site_logo`, `favicon`, `hero_image` (separate from Constance because they're file uploads). Access via `SiteSettings.get_settings()` or `site_settings` template context variable.

`LOGO_DISPLAY_MODE` Constance setting: `name_only` (default), `logo_only`, `logo_and_name`. Falls back to team name if no logo uploaded. Managed at `/site/config/` "Site Images" section.

## Home Page Logic

Home page (`gotta_bike_platform/views.py: home()`): uses `HOME_PAGE_SLUG_AUTHENTICATED` for logged-in users, `HOME_PAGE_SLUG` for anonymous, falls back to `templates/index.html`.

## Analytics

Client-side JS in `base.html` sends page data to `/api/analytics/track/` (Django Ninja). `PageVisit` model stores combined server+client data. Dashboard at `/analytics/` (requires `app_admin`). Key files in `apps/analytics/`.

## Strava Integration

`apps/club_strava/` - Strava club activity sync. Token refresh is automatic on 401 (tokens saved to Constance). Activity list at `/strava/`, manual sync at `/strava/sync/`. Constance settings: `STRAVA_CLUB_ID`, `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_ACCESS_TOKEN`, `STRAVA_REFRESH_TOKEN` (tokens auto-updated).

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

Configured in `settings.py` (top: `logfire.configure()`, end: `instrument_django()`/`instrument_httpx()`).
Env vars: `LOGFIRE_TOKEN` (optional), `LOGFIRE_ENVIRONMENT`. Usage: `import logfire` then `logfire.info/warning/error("msg", key=val)`.

### Logging Requirements

Add logfire logging for: API calls, error handlers, auth/permission checks, background tasks, data operations, form submissions. Never silently catch exceptions — always `logfire.error("msg", error=str(e))`. Use `logfire.span()` for multi-step operations. Include context: `user_id`, `discord_id`, `zwid`.

Levels: `error` (failures/exceptions), `warning` (rate limits/fallbacks), `info` (operations/actions), `debug` (counts/diagnostics).

## Guild Member Sync

Syncs Discord guild members with Django to track membership status.

`GuildMember` model (`apps/accounts/models.py`) stores Discord member data with OneToOne link to User (matched by `discord_id`). Bot POSTs to `/api/dbot/sync_guild_members`; members not in payload get `date_left` set. Auto-syncs every 6 hours.

**Important**: Only affects Discord OAuth users — regular Django accounts without `discord_id` are not modified.

### Discord Review Page

Admin page at `/team/discord-review/` (requires `membership_admin`). Lists GuildMember records with search, role filters (include/exclude), date range, sortable columns. See `discord_review_view` in `apps/team/views.py`.

## Race Ready Verification

Users can achieve "Race Ready" status by completing verification requirements. This status gates participation in
official team races.

### Terminology

"Race Verified" and "Race Ready" have the same meaning and are used interchangeably. The UI/UX uses "Race Verified"
while the backend code uses "Race Ready" (e.g., `is_race_ready`, `RaceReadyRecord`, `RACE_READY_ROLE_ID`).

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
2. Record includes `record_date` (date of the evidence) and optional `same_gender` flag (requires same-gender reviewer)
3. Record starts in `pending` status
4. Users with `approve_verification` permission review and verify/reject records
5. Verified records expire based on `record_date` (not submission date) and Constance settings:
   - `WEIGHT_FULL_DAYS` (default: 180 days)
   - `WEIGHT_LIGHT_DAYS` (default: 30 days)
   - `HEIGHT_VERIFICATION_DAYS` (default: 0 = never expires)
   - `POWER_VERIFICATION_DAYS` (default: 365 days)
6. `RaceReadyRecord.days_remaining` property returns days until expiration (or None)

### Race Ready Role Assignment

Discord bot assigns `RACE_READY_ROLE_ID` role when `is_race_ready` is True (via `/my_profile` and `/sync_user_roles` endpoints). Set `RACE_READY_ROLE_ID` to `0` to disable. Roster at `/team/roster/` shows race ready status.

## Membership Registration

New member registration workflow integrated with Discord.

### Terminology

The codebase uses "Application" in model/URL names (e.g., `MembershipApplication`, `/team/applications/`) but the
user-facing terminology should be "Registration" or "Membership Registration". This applies to:
- UI labels and headings
- Documentation
- User communications

### Workflow

1. User submits modal in Discord (`join_the_coalition` cog)
2. Bot POSTs to API, creates `MembershipApplication` record
3. User receives DM with UUID link to complete registration
4. User fills out required fields (name, agreements, profile info)
5. Membership admin reviews and approves/rejects
6. If approved, user can login via Discord OAuth

### MembershipApplication Model (`apps/team/models.py`)

**Status Choices:**

| Status | Description |
|--------|-------------|
| `pending` | Awaiting review |
| `in_progress` | Admin is reviewing |
| `waiting_response` | Waiting for user to provide additional info |
| `approved` | Registration approved |
| `rejected` | Registration rejected |

**Key Fields:**

- `id` - UUID primary key for secure, unguessable URLs
- `discord_id` - Discord user ID (unique)
- `discord_username`, `server_nickname` - Discord names
- `first_name`, `last_name` - Registrant's name
- `agree_privacy`, `agree_tos` - Agreement flags
- `zwift_id`, `country`, `timezone`, `birth_year`, `gender` - Profile fields
- `trainer`, `power_meter`, `dual_recording` - Equipment fields
- `admin_notes`, `status`, `modified_by` - Admin fields

**Properties:**

- `is_complete` - True if required fields are filled
- `is_editable` - True if registrant can still edit (not approved/rejected)
- `is_actionable` - True if admin can approve/reject

### API Endpoints

- `POST /api/dbot/membership_application` - Create registration (returns existing if discord_id exists)
- `GET /api/dbot/membership_application/{discord_id}` - Get registration by Discord ID

### Permissions

Users with `membership_admin` permission can:
- View all registrations at `/team/applications/`
- Review and update registration status
- Add admin notes

Configure `PERM_MEMBERSHIP_ADMIN_ROLES` in Constance with Discord role IDs.

### Discord Notifications

Registration updates posted to `REGISTRATION_UPDATES_CHANNEL_ID` (set to `0` to disable). Events: new registration, applicant update, status change, admin notes. Background task `notify_application_update()` in `apps/team/tasks.py` — enqueued async, skips gracefully if not configured.
