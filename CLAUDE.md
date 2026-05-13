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
    - `DiscordChannel` - Discord channels synced from server for Select dropdowns in Event/Squad forms
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
- `events` - Event management with squads and signups:
    - `Event` model - Team events with title, description (Markdown), dates, Discord channel/role IDs (`head_captain_role_id` for the event's head captain), signup settings, `prefixes` (JSONField list of allowed Discord role/channel prefixes), `squad_gender_options` (JSONField list, default `["Male", "Female", "COED"]`), `squad_gender_required` (bool — gates whether the field appears in the signup form)
    - `Squad` model - Squads within an event with captain/vice-captain, ZR category range, Discord text/audio channels, URL, invite URL, optional `gender` (single value drawn from the parent event's `squad_gender_options`)
        - Discord role fields: `team_discord_role` (squad role), `discord_captain_role` (squad captain role), `captain_notifications` (bool — DM captains on member events)
    - `SquadMember` model - Links users to squads (member/pending/rejected status, unique on squad+user)
    - `AvailabilityGrid` - Date/time grid for collecting squad availability (status: draft/published/closed). Stores all times in UTC; converted to user/grid timezone at render
    - `AvailabilityResponse` - One per (grid, user); stores `available_cells` JSON of UTC date/time pairs
    - `AvailabilitySlotSelection` ("Scheduled Race") - Named race slot built from heatmap by captains/admins. Fields: `name`, `slot_date/slot_time` (UTC), `status` (none/pending/confirmed), `event_invite_url`, `course_url`, `thread_link`, `selected_users` M2M
    - `EventSignup` model - Event-level signups with status, optional multi-select `signup_timezone` and `signup_squad_gender` (only saved when the event has the corresponding `*_required` flag set)
    - `Race` model - Individual races within an event
    - `RaceRegistration` model - Race-level registrations
    - Views require `team_member` permission; create/edit/delete event/squad require `event_admin`
    - **Manage availability** (grids, scheduled races, Discord thread creation) is gated by `_can_manage_squad_availability(user, squad)` — allows event admins, superusers, the squad's captain/vice-captain, holders of the squad's `discord_captain_role`, and holders of the parent event's `head_captain_role_id`. Same captain-or-admin pattern is used by `_can_view_v_report`
    - **Discord thread actions** for a confirmed scheduled race: "Save & Create Thread" (creates the thread + posts a starter message) and "Save & Post Update" (posts a "Race details updated" message into the existing thread). Both go through `apps/accounts/discord_service.py:create_discord_thread` / `send_discord_channel_message`; the resulting URL lands on `slot.thread_link`. Requires status=confirmed, riders selected, squad has `discord_channel_id`
    - Role Setup: event prefixes (multi-select from `EVENT_ROLE_PREFIXES` constance), head captain role, event role — editable by `assign_roles` or event's head captain
    - Manage Roles: assign/unassign Discord roles to members — requires `assign_roles` or event's head captain role
    - Squad Discord roles must start with **any** of the event's prefixes; the squad role dropdown is disabled when the event has no prefixes set
    - Squad assignment from signup list (event admins can assign users to multiple squads)
    - Expandable squad member list with ZP/ZR data, Discord role checks
    - Markdown rendering for event description and signup instructions
- `magic_links` - Passwordless authentication (legacy)
- `user_api` - Per-user API keys with bearer auth (Django Ninja):
    - `ApiKey` model — 30-day default expiry, hashed at rest, scoped to a single user
    - `/user/api-keys/` — UI for creating/revoking the current user's keys
    - `/api/user/` — bearer-authenticated endpoints (e.g. `zr_profile`)
    - `purge_expired_api_keys` background task (in `apps/dbot_api/cron_api.py` task registry) hard-deletes keys expired > 90 days
- `tickets` - **Internal only** (sidebar link intentionally disabled). Member-support and team-management ticket queue:
    - `Ticket` model — `title`, `details` (Markdown), `status` (new/in_progress/closed), `category` (support/membership/verification/equipment/discord/event/squad/other), `priority` (low/normal/high/urgent), `submitted_by` (nullable for system-generated tickets), `assigned_to`, `closed_by`, `resolution`, `guild_member` (FK to `accounts.GuildMember` for system-generated tickets)
    - `closed_at` is auto-managed by `Ticket.save()` when status transitions to/from `closed`; `closed_by` is set in the view that triggered the close
    - `apps/tickets/services.py:create_member_left_ticket` is called by the guild-member sync when a member's `date_left` is freshly stamped — files a low-priority Membership ticket with a Markdown summary, idempotent while a non-closed ticket exists for the same `GuildMember`
    - Routes at `/tickets/` (list with filters, create, detail, edit) — gated by `team_member_required`; no finer-grained permissions yet
- `cms` - Dynamic CMS pages:
    - `Page` model - Content pages with markdown, hero sections, card layouts, and accordion sections
    - Publishing workflow (draft/published status)
    - Navigation settings (show_in_nav, nav_location, nav_order, nav_title)
    - `nav_location` choices: `main_nav` (sidebar, default) or `user_menu` (top-right user dropdown)
    - Context processor provides `cms_nav_pages` (sidebar) and `cms_user_menu_pages` (user dropdown)
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
- `performance_verification_team` - Performance verification team member; can view media on reviewed records, change status of any record, and delete any verification record
- `data_connection` - Access to Google Sheets data exports
- `pages_admin` - Can create and manage CMS pages
- `event_admin` - Create, edit, and manage events, squads, and signups
- `assign_roles` - Manage Discord role setup and assign/unassign Discord roles on events; event Head Captain Role holders also get this ability per-event

#### Constance Permission Settings

Configure in Django admin at `/admin/constance/config/` under "Permission Mappings":

- `PERM_APP_ADMIN_ROLES` - JSON array of Discord role IDs, e.g., `["1234567890123456789"]`
- `PERM_TEAM_CAPTAIN_ROLES`, `PERM_VICE_CAPTAIN_ROLES`, `PERM_LINK_ADMIN_ROLES`, etc.
- `PERM_APPROVE_VERIFICATION_ROLES` - Role IDs that can approve/reject verification records
- `PERM_PERFORMANCE_VERIFICATION_TEAM_ROLES` - Role IDs for performance verification team
- `PERM_DATA_CONNECTION_ROLES` - Role IDs that can access data exports
- `PERM_PAGES_ADMIN_ROLES` - Role IDs that can manage CMS pages
- `PERM_EVENT_ADMIN_ROLES` - Role IDs that can manage events, squads, and signups
- `PERM_ASSIGN_ROLES` - Role IDs that can manage Discord role setup and assign/unassign roles on events

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

### Scheduler (in-process APScheduler)

`gotta_bike_platform/management/commands/scheduler.py` runs a `BlockingScheduler` with one `IntervalTrigger` per job. Run as a separate service via `uv run python manage.py scheduler`; jobs enqueue Django tasks (the `db_worker` still executes them).

- **Job registry** — `_get_scheduled_jobs()` lists each task path and reads its cadence from a `SCHEDULER_*_HOURS` Constance setting (default 6h). Interval changes require a scheduler restart to take effect (the docstring at the top of the file is authoritative).
- **UI** — `/site/config/scheduler/` (driven by the `Scheduler` group in `CONSTANCE_CONFIG_FIELDSETS`) lets admins adjust the cadences.
- **When adding a new scheduled task**: add a `SCHEDULER_*_HOURS` setting, add the task to `_get_scheduled_jobs()`, and add it to the `Scheduler` fieldset so it appears on the config page.

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
- `apps/accounts/discord_service.py` - Direct Discord REST client (httpx, sync). Bot token from `config.DISCORD_BOT_TOKEN`. Functions: `send_discord_dm`, `send_discord_channel_message` (supports `allowed_user_ids` for proper @-mention notifications), `send_verification_notification`, `add_discord_role`, `remove_discord_role`, `sync_user_discord_roles`, `create_discord_thread` (returns `(thread_id, error)`). The Discord bot has no HTTP server — all web→Discord calls go through this module.

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
    - `POST /api/dbot/sync_guild_channels` - Sync all Discord channels
    - `POST /api/dbot/sync_guild_members` - Bot-driven sync fallback; the platform now drives this itself (see Guild Member Sync)
    - `POST /api/dbot/sync_user_roles/{discord_id}` - Sync a user's roles
    - `POST /api/dbot/roster_filter` - Create filtered roster link from Discord channel members
    - `POST /api/dbot/membership_application` - Create new membership registration from Discord
    - `GET /api/dbot/membership_application/{discord_id}` - Get membership registration by Discord ID
    - `POST /api/dbot/update_zp_team` - Trigger ZwiftPower team update task
    - `POST /api/dbot/update_zp_results` - Trigger ZwiftPower results update task

### Cron API (`apps/dbot_api/cron_api.py`)

REST API for triggering tasks by name (used by the in-process Scheduler above, by `/site/config/background_tasks/`, and historically by an external cron service):

- Auth: `X-Cron-Key` header (uses same `DBOT_AUTH_KEY` from constance)
- Endpoints:
    - `GET /api/cron/tasks` - List available tasks
    - `POST /api/cron/task/{task_name}` - Trigger a task by name
- Available tasks: see `TASK_REGISTRY` in `apps/dbot_api/cron_api.py` — covers ZP/ZR/Strava/YouTube/Discord
  syncs, race-ready cache refresh, expiry warning DMs, data-connection exports, and more.

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
- `/user/api-keys/` - Per-user API key management (`apps.user_api.urls`)
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
- `/events/` - Events management (`apps.events.urls`):
    - `/events/` - Event list
    - `/events/my-events/` - Authenticated user's signed-up events with squad/availability state
    - `/events/create/` - Create event (event admins)
    - `/events/<id>/` - Event detail with signups, squads, and squad assignment
    - `/events/<id>/edit/` - Edit event (event admins)
    - `/events/<id>/signup/` - Sign up for event
    - `/events/<id>/squads/add/` - Create squad (event admins)
    - `/events/<id>/squads/<squad_id>/edit/` - Edit squad (event admins)
    - `/events/<id>/squads/<squad_id>/delete/` - Delete squad (event admins, POST)
    - `/events/<id>/squads/assign/` - Assign user to squad (event admins, POST)
    - `/events/<id>/role-setup/` - Discord role setup (event admins read-only; assign_roles/head captain can edit)
    - `/events/<id>/manage-roles/` - Assign/unassign Discord roles (assign_roles or head captain)
    - `/events/<id>/squads/<squad_id>/availability/` - Manage availability grids for a squad (captain/admin gate)
    - `/events/<id>/squads/<squad_id>/availability/<grid_uuid>/` - Member response form (published grids)
    - `/events/<id>/squads/<squad_id>/availability/<grid_uuid>/results/` - Heatmap + scheduled race editor
    - `/events/<id>/squads/<squad_id>/availability/<grid_uuid>/slots/<slot_pk>/create-thread/` - Create Discord thread for a confirmed scheduled race (powers "Save & Create Thread")
    - `/events/<id>/squads/<squad_id>/availability/<grid_uuid>/slots/<slot_pk>/post-update/` - Persist edits and post an update message into the slot's existing thread (powers "Save & Post Update")
- `/site/config/` - Site configuration (Constance settings UI)
- `/data-connections/` - Google Sheets exports (`apps.data_connection.urls`)
- `/strava/` - Strava club activities (`apps.club_strava.urls`)
- `/analytics/` - Analytics dashboard (`apps.analytics.urls`, requires `app_admin`)
- `/tickets/` - Member-support / team-management tickets (`apps.tickets.urls`) — **internal only**, sidebar link disabled
    - `/tickets/` - List with status/category/mine/search filters
    - `/tickets/new/` - Create
    - `/tickets/<int:pk>/` - Detail
    - `/tickets/<int:pk>/edit/` - Edit (assignee / status / resolution)
- `/api/dbot/` - Discord bot API
- `/api/cron/` - Cron task API
- `/api/user/` - Per-user API keys (Django Ninja), bearer-authenticated
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

Settings are grouped: Team Identity, Zwift Credentials, API Keys, Permission Mappings (`PERM_*_ROLES` — JSON arrays of Discord role IDs), Discord Guild (`EVENT_ROLE_PREFIXES` — JSON array of allowed event prefix characters), Discord Roles/Channels, New Arrival Messages (support `{member}`/`{server}` placeholders), Verification (`CATEGORY_REQUIREMENTS` JSON, `*_DAYS` expiration settings, `VERIFICATION_FORM_MESSAGE` Markdown message shown on verification form), Google, Strava, Site Settings.

Usage: `from constance import config; config.SETTING_NAME`. Add new settings in `settings.py` under `CONSTANCE_CONFIG`.

### Site Image Settings

`SiteSettings` singleton model (`gotta_bike_platform/models.py`) stores `site_logo`, `favicon`, `hero_image`, `not_verified_emoji`, `verified_emoji`, `extra_verified_emoji` (separate from Constance because they're file uploads). Access via `SiteSettings.get_settings()` or `site_settings` template context variable.

`LOGO_DISPLAY_MODE` Constance setting: `name_only` (default), `logo_only`, `logo_and_name`. Falls back to team name if no logo uploaded. Managed at `/site/config/` "Site Images" section.

## Home Page Logic

Home page (`gotta_bike_platform/views.py: home()`): uses `HOME_PAGE_SLUG_AUTHENTICATED` for logged-in users, `HOME_PAGE_SLUG` for anonymous, falls back to `templates/index.html`.

## Analytics

Client-side JS in `base.html` sends page data to `/api/analytics/track/` (Django Ninja). `PageVisit` model stores combined server+client data. Dashboard at `/analytics/` (requires `app_admin`). Key files in `apps/analytics/`.

## Notification Badges

Sidebar/avatar badges are driven by context processors with short per-user caches:
- `apps.team.context_processors.pending_verification_count` — count of `RaceReadyRecord.status=PENDING` the current user can review (mirrors same-gender gate from `verification_records_view`). Sidebar badge on "Verification Records".
- `apps.events.context_processors.pending_availability_count` — count of published `AvailabilityGrid` rows in the user's squads with no response yet. Drives the warning dot on the avatar and the count next to "My Events" in the user-menu dropdown.

Both gate on permission/auth before any DB call, then cache the count for 60 s per user. New badges should follow this pattern (skip the query when the user can't act on it; cache short).

## Strava Integration

`apps/club_strava/` - Strava club activity sync. Token refresh is automatic on 401 (tokens saved to Constance). Activity list at `/strava/`, manual sync at `/strava/sync/`. Constance settings: `STRAVA_CLUB_ID`, `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_ACCESS_TOKEN`, `STRAVA_REFRESH_TOKEN` (tokens auto-updated).

## Testing

Tests use **pytest + pytest-django**. Config lives in `[tool.pytest.ini_options]` in `pyproject.toml`; shared fixtures in the top-level `conftest.py`.

- Discovery: `tests.py`, `test_*.py`, `*_tests.py` under `apps/` and `gotta_bike_platform/`.
- Test DB is built from current model state via `--no-migrations` (migrations are **not** replayed). `--reuse-db` keeps the test DB between runs — pass `--create-db` after a model change to force regeneration.
- Why `--no-migrations`: a latent bug in `apps/accounts/migrations/0013_add_is_race_ready_cached_field.py` makes fresh migrations fail (the data migration imports the live `User` model, which selects columns added in 0017). Production missed this because 0013 ran before 0017 was created. Tracked in TODO.md P0; remove `--no-migrations` once fixed.

### Shared fixtures (`conftest.py`)

All user fixtures depend on `db` and grant permissions via `User.permission_overrides`, so tests don't depend on Constance or Discord roles.

- `user_model` — the active User class
- `user` — plain user, no permissions
- `team_member` — `team_member` permission
- `app_admin` — `app_admin` + `team_member`
- `event_admin` — `event_admin` + `team_member`
- `superuser` — `is_superuser=True` (bypasses all checks)
- `auth_client` — `pytest-django`'s `client` force-logged-in as `team_member`
- `admin_authed_client` — `client` force-logged-in as `app_admin`

Add new permission fixtures in `conftest.py` following the same pattern (`_make_user(..., permissions={...})`). Use feature-local `conftest.py` files for app-specific fixtures (e.g. an `event_factory` in `apps/events/conftest.py`).

### Writing tests

- Tag DB-touching tests with `@pytest.mark.django_db` (built-in `db` fixture also works).
- Per-file ruff ignores are configured for `**/tests.py`, `**/test_*.py`, and `conftest.py` — `assert`, missing module/function docstrings, and `DOC201` are allowed.
- For new tests, prefer the shared fixtures over rolling your own User in each test. If you need a variant (different gender, ZP category, etc.) build it from a fixture rather than calling `create_user` directly.
- Avoid making real HTTP calls — patch `httpx` clients (`apps/zwiftpower/zp_client.py`, `apps/zwiftracing/zr_client.py`, `apps/accounts/discord_service.py`, etc.) at the client boundary.

### Running

```bash
uv run pytest                               # full suite
uv run pytest apps/events                   # one app
uv run pytest apps/accounts/tests.py::test_app_admin_has_app_admin_permission
uv run pytest -k permission                 # by keyword
uv run pytest --create-db                   # rebuild test DB
```

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

`GuildMember` model (`apps/accounts/models.py`) stores Discord member data with OneToOne link to User (matched by `discord_id`).

**Sync drivers** (both go through `apps/accounts/services.py:apply_guild_member_sync`, which owns the upsert/depart logic):

- **Primary**: `sync_guild_members` background task (`apps/accounts/tasks.py`) calls Discord's REST API directly via `apps/accounts/services.py:fetch_guild_members_from_discord` (paginated, 429-aware). Scheduled by the in-process APScheduler — cadence is `SCHEDULER_SYNC_GUILD_MEMBERS_HOURS` Constance setting (default 6h). Also triggerable from `/site/config/background_tasks/` and via the cron API.
- **Fallback**: `POST /api/dbot/sync_guild_members` — Discord-bot push, accepts the same normalized payload and delegates to the same service.

When a previously-active member is missing from a sync, `date_left` is stamped and `apps/tickets/services.py:create_member_left_ticket` files a low-priority Membership ticket (idempotent while a non-closed ticket exists for that member). See the `tickets` app section.

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

`User.is_race_ready` is a **cached BooleanField** (not a property). The live calculation lives in
`User.calculate_race_ready()` and uses `CATEGORY_REQUIREMENTS`. The cache is updated by:
- `User.refresh_race_ready()` — call after any change that affects a user's verification state
- `refresh_all_race_ready` cron task — periodic full sweep, also handles expiration

There is no Django signal — code paths that mutate `RaceReadyRecord` must call `refresh_race_ready()` themselves
(see existing call sites in `apps/team/views.py`).

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

### Verification Form

The `/user/verification/` page displays a Markdown message from `VERIFICATION_FORM_MESSAGE` (Constance setting) below the card title, rendered via `render_markdown` template filter.

### Verification Emojis

Custom emoji/icon images for verification status are stored on `SiteSettings` (not Constance, since they're file uploads):

- `not_verified_emoji` - Shown for not-verified status
- `verified_emoji` - Shown for verified status
- `extra_verified_emoji` - Shown for extra-verified status

Managed via `/site/config/` "Site Images" section or Django admin. Accessible in templates via `site_settings.not_verified_emoji` etc.

When uploaded, these emojis replace the default colored badges/SVGs for race verified status across all templates:
- `base.html` - Header status text and user dropdown menu
- `accounts/verification.html` - Verification page title icon
- `accounts/public_profile.html` - Public profile Race Verified row
- `team/roster.html` - Roster Race Verified column
- `events/event_detail.html` - Signup list and squad member list
- `events/event_form.html` - Event edit signup list

Falls back to original badges/SVGs when no emoji image is uploaded.

### Verification Flow

1. User submits a `RaceReadyRecord` (weight, height, or power photo) via the web app
2. Record includes `record_date` (date of the evidence) and optional `same_gender` flag (requires same-gender reviewer)
3. Record starts in `pending` status
4. Users with `approve_verification` permission review and verify/reject records; reviewers can edit `record_date` before acting
5. Users with `performance_verification_team` or `app_admin` permission can change the status of or delete any verification record (any status)
6. Verified records expire based on `record_date` (not submission date) and Constance settings:
   - `WEIGHT_FULL_DAYS` (default: 120 days)
   - `WEIGHT_LIGHT_DAYS` (default: 30 days)
   - `HEIGHT_VERIFICATION_DAYS` (default: 0 = never expires)
   - `POWER_VERIFICATION_DAYS` (default: 365 days)
   - `EXPIRE_WARNING_DAYS` (JSON list of int days, default `[15, 7, 3, 1]`) — the `warn_expiring_verifications` cron task DMs each owner once per matching threshold day
7. `RaceReadyRecord.days_remaining` property returns days until expiration (or None)

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
