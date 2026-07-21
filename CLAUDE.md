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
uv run python manage.py ensuresuperuser        # Idempotent bootstrap: no-op if a superuser exists, otherwise creates one from SUPERUSER_USERNAME / SUPERUSER_PASSWORD / SUPERUSER_EMAIL env vars (used on Railway deploys)

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
- Required env vars: `SECRET_KEY`, `DATABASE_URL` (defaults exist for local dev only — must be set in production)
- Optional env vars: `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` (OAuth)
- Optional env vars: `LOGFIRE_TOKEN`, `LOGFIRE_ENVIRONMENT` (observability)
- Optional env vars: `SUPERUSER_USERNAME`, `SUPERUSER_PASSWORD`, `SUPERUSER_EMAIL` (read by `manage.py ensuresuperuser` for first-deploy bootstrap)
- Runtime settings (via constance): API credentials and team settings (see Dynamic Settings below). **Note**: code that does `from constance import config` (e.g. `config.DISCORD_BOT_TOKEN`, `config.GUILD_ID`) reads from constance, *not* from `gotta_bike_platform/config.py` — the two `config` objects are unrelated.

### Static Files & Storage

- **Static files**: WhiteNoise (compressed, cached, served from memory). `collectstatic` writes to `staticfiles/`.
- **Media files**: S3-compatible storage when configured (Railway), otherwise local filesystem.
- S3 env vars (optional): `AWS_S3_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_REGION_NAME`

### Apps (in `apps/`)

Read each app's `models.py` for full field lists. Bullets below capture purpose + cross-app interactions + non-obvious behavior only.

- `accounts` - Custom User model (Discord/Zwift fields), django-allauth adapters, role-based permissions. Key entry points: `decorators.py` (`discord_permission_required`, `team_member_required`), `GuildMember` (Discord member tracking — see Guild Member Sync), `YouTubeVideo` (RSS-fetched videos for Team Feed)
- `team` - Core team management. Models: `RaceReadyRecord` (see Race Ready Verification), `TeamLink`, `RosterFilter` (**5-min expiration**), `MembershipApplication` (see Membership Registration), `DiscordRole` / `DiscordChannel` (synced from server, used as Select dropdown choices in Event/Squad forms). Services: `get_unified_team_roster()` merges ZP + ZR + User data; `get_user_verification_types(user)` returns required verification types per ZP category
- `zwift` - Zwift integration. `utils.fetch_zwift_id(username, password)` calls the Sauce mod API to resolve a Zwift account to a `zwid` (used during onboarding/profile linking); models/views are stubs.
- `zwiftpower` - ZwiftPower API integration. Models: `ZPTeamRiders`, `ZPEvent`, `ZPRiderResults`. Client in `zp_client.py` (session-based, requires Zwift OAuth login)
- `zwiftracing` - Zwift Racing API integration. `ZRRider` stores per-discipline `seed_*` and `velo_*` rating fields. Client in `zr_client.py` returns `(status_code, json)` tuples; 429s return data with `retryAfter` instead of raising
- `analytics` - Server-side page-visit tracking enriched by a client-side JS snippet in `base.html`. Dashboard at `/analytics/` (`app_admin` only). Tracking endpoint: `POST /api/analytics/track/` (Django Ninja)
- `club_strava` - Strava club activity sync. See Strava Integration section
- `dbot_api` - Discord bot REST API using Django Ninja (see Discord Bot API section). The task registry it used to host has moved to `gotta_bike_platform/task_registry.py`.
- `data_connection` - Configurable Google Sheets exports via service account. Field selection across User/ZP/ZR, filters by gender/division/rating/phenotype. **Manual sync clears the sheet and rewrites all data**
- `events` - Event management with squads, signups, availability grids, scheduled races, and Discord thread integration. See Event Permission Gates below for the load-bearing behavior
- `magic_links` - Passwordless authentication (legacy — kept so old DMed links still resolve at `/m/`; do not extend)
- `user_api` - Per-user API keys with bearer auth (Django Ninja). `ApiKey`: 30-day default expiry, hashed at rest, scoped to one user. `purge_expired_api_keys` scheduled task hard-deletes keys expired > 90 days
- `tickets` - **Internal only** (sidebar link intentionally disabled). Member-support / team-management ticket queue. Non-obvious: `Ticket.closed_at` is auto-managed by `save()` on status transitions to/from `closed`; `apps/tickets/services.py:create_member_left_ticket` fires from the guild-member sync when `date_left` is freshly stamped (idempotent while a non-closed ticket exists for that `GuildMember`). Gated by `team_member_required`; no finer-grained permissions yet
- `cms` - Dynamic CMS pages (`Page` model) with markdown body, draft/published workflow, sidebar/user-menu placement (`nav_location` = `main_nav` or `user_menu`), per-page `require_login` / `require_team_member`. Context processor exposes `cms_nav_pages` + `cms_user_menu_pages`
- `zwift_data` - **Canonical, single source of truth** for Zwift worlds/routes/segments, synced from the [Zwift Speed Lab](https://zwiftspeedlab.coalitionracing.com) `/api/data/all.zip` bundle. Models `ZwiftWorld` / `ZwiftRoute` / `ZwiftSegment` + a `ZwiftDataset` version singleton. **The planners FK straight to `ZwiftRoute`** (`ttt_planner.TttPlan.route`, `ladder_planner.LadderMatchup.route`). `ZwiftRoute` also carries **curated** fields not in the dataset — the ZwiftRacing vELO2 Race weights (`velo_sprint/punch/climb/endurance/pursuit` as percent, + `velo_num_events`) plus `recommended_laps` / `supports_laps` — with `VELO_FACTOR_META` / `has_velo_factors` / `velo_factor_bars()` (same API the ladder `compute.py` uses). `services/sync.py:sync_dataset()` downloads the bundle, stores `routes.json` / `segments.json` / `route_profiles.json` in object storage (bucket) under `zwift_data/`. **Route sync upserts by `name_hash` (writing only `ZwiftRoute.SYNCED_FIELDS`) so curated vELO/laps survive a re-sync and row PKs stay stable for the FKs**; worlds/segments are delete-and-recreate. `catalog.py` serves the bulk geometry (per-route elevation/GPS profile, route↔segment crossings) from storage via a **synced_at-stamped in-process cache** (reloads only when a newer sync lands — safe across web workers). `services/velo.py` imports the ZwiftRacing routes JSON (`apps/zwiftracing/docs/ZwiftRacing Routes VELO WEIGHTS.json`) joining by `routeId == name_hash` — via `manage.py import_velo_weights` or the "Load vELO weights" button (`routes:load_velo`, `racing_admin`). Scheduled weekly (`SCHEDULER_SYNC_ZWIFT_DATA_HOURS`); manual seed via `manage.py sync_zwift_data`. Source of truth for the `/routes/` reference page — see Routes Page below
- `ttt_planner` - TTT planner + the shared `/routes/` reference page. Owns `TttPlan` / `PlanRider` / `PowerUp` (power-ups are locally curated). **`Route`/`Segment` were retired** (migration `ttt_planner/0022`) — routes/segments now come entirely from `apps.zwift_data`; the planner route pickers (`terrain.route_options()`, `ladder_planner.services.courses.route_options()`) read `ZwiftRoute` cycling routes. `worlds.py` + `data/*.json` remain only because historical migrations import them

#### Event Permission Gates (`apps/events/views.py`)

Read these helpers before touching event/squad views — most non-trivial behavior in the events app routes through them.

- `_can_manage_event_squads(user, event)` — gates squad CRUD. Event admins, superusers, and holders of the event's `head_captain_role_id` Discord role
- `_can_manage_squad_availability(user, squad)` — gates availability grids, scheduled races, and Discord thread actions. Adds squad captain/vice-captain and squad `discord_captain_role` holders to the set above. Same gate used by `_can_view_v_report`
- Squad Discord roles **must** start with one of the event's `prefixes` (server-side re-validated against `DiscordRole` even if the client tampers with the choices list). The squad-role dropdown is disabled when the event has no prefixes set
- `coordinator_role_ids` ("Regional/Group Coordinators") — multi-select restricted to roles starting with `EVENT_ROLE_PREFIXES`; same server-side re-validation
- Role Setup (`/events/<id>/role-setup/`): editable by `assign_roles` or event head captain. Manage Roles (`/events/<id>/manage-roles/`): same gate
- Discord thread actions ("Save & Create Thread" / "Save & Post Update") require `status=confirmed`, riders selected, and `squad.discord_channel_id`. Both go through `apps/accounts/discord_service.py`; the resulting URL lands on `slot.thread_link`. Captain, vice-captain, and substitute are added to `allowed_user_ids` so they get pinged even when not racing
- `signup_notification_channel_id` on `Event`: `0` disables per-rider signup notifications
- All grid/response/slot times stored in UTC, converted at render. `EventSignup.signup_timezone` and `signup_squad_gender` are only saved when the matching `*_required` flag is on

### Authentication (django-allauth)

- Discord OAuth only (no username/password)
- **Guild membership required**: Users must be a member of the configured Discord server (`GUILD_ID`) to sign up or log
  in
- Custom User model fields: `discord_id`, `discord_username`, `discord_nickname`, `zwid`,
  social fields (`strava_url`, `youtube_channel`, `youtube_channel_id`, `twitch_channel`, `instagram_url`,
  `facebook_url`, `twitter_url`, `tiktok_url`, `bluesky_url`, `mastodon_url`, `garmin_url`, `tpv_profile_url`),
  equipment fields (`trainer`, `powermeter`, `dual_recording`, `heartrate_monitor`)
- TOTP two-factor authentication via `allauth.mfa`
- Custom adapter at `apps/accounts/adapters.py` verifies guild membership, syncs Discord profile data, redirects rejected users to `DISCORD_URL` — see "Discord OAuth Adapter" below for the load-bearing gotchas
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
- Shortcut for the common "must be a team_member" case: `@team_member_required()` (from `apps.accounts.decorators`) — wraps `discord_permission_required("team_member")` and is what most app views use (tickets, zwiftpower, user_api, events, etc.)

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

- **Task registry** — `gotta_bike_platform/task_registry.py:TASK_REGISTRY` is the single source of truth for scheduled and manually-triggerable tasks. The scheduler calls `get_scheduled_tasks()` (filters `scheduled=True`, resolves each `hours_setting` Constance value). The admin "Run Now" UI at `/site/config/background_tasks/` reads the same dict via `_get_task_registry()` in `apps/accounts/views.py`.
- **UI** — `/site/config/scheduler/` (driven by the `Scheduler` group in `CONSTANCE_CONFIG_FIELDSETS`) lets admins adjust the cadences. Interval changes require a scheduler restart.
- **When adding a new scheduled task**: import the task in `task_registry.py`, add an entry with `scheduled=True` and a `hours_setting` pointing at a new `SCHEDULER_*_HOURS` Constance setting, then add that key to the `Scheduler` fieldset. For a manual-trigger-only task (no schedule), omit `scheduled` (or set to `False`); no Constance setting needed.

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
    - `GET /api/dbot/bot_config` - Bot configuration (constance values the bot needs on startup / hourly refresh)
    - `GET /api/dbot/recent_videos` - 5 most recent team-feed YouTube videos + team-feed URL
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

### URL Routes (`gotta_bike_platform/urls.py`)

Mount points — read each app's `urls.py` for the full pattern list:

- `/`, `/about/` — `gotta_bike_platform.views` (home — see Home Page Logic)
- `/admin/`, `/accounts/`, `/site/config/` — Django admin, allauth, Constance UI
- `/user/`, `/user/api-keys/` — `apps.accounts.urls`, `apps.user_api.urls`
- `/team/`, `/events/`, `/tickets/`, `/page/<slug>/` — feature apps (`tickets` is **internal only**, sidebar link disabled)
- `/strava/`, `/zp/`, `/analytics/`, `/data-connections/` — feature apps
- `/api/dbot/`, `/api/user/`, `/api/analytics/` — Django Ninja APIs
- `/m/` — magic links (legacy — see Apps section)

Non-obvious gates / behavior not visible from the URL pattern alone:

- `/team/roster/f/<uuid>/` — filtered roster, **5-min expiration**
- `/team/apply/<uuid>/` — public (no auth) membership registration form
- `/events/<id>/squads/manage|add|edit|delete/` — `_can_manage_event_squads` (event admin / superuser / event head captain role holder)
- `/events/<id>/squads/<sid>/availability/...` — `_can_manage_squad_availability` (above + squad captain/vice-captain + `discord_captain_role` holders)
- `/events/<id>/role-setup/` — event admins read-only; `assign_roles` or head captain can edit
- `/events/<id>/manage-roles/` — `assign_roles` or event head captain only
- `/analytics/` — `app_admin` only
- `/robots.txt` — dynamic (rendered by `gotta_bike_platform/views.py`)

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

## Routes Reference Page (`/routes/`)

Served by `apps.ttt_planner` views but driven by the canonical `zwift_data` dataset (see the `zwift_data` app bullet). Mounted via `apps/ttt_planner/routes_urls.py` (namespace `routes:`).

- **Tabs**: Routes / Worlds / Segments come from `ZwiftRoute` / `ZwiftWorld` / `ZwiftSegment`; Power-ups stays `ttt_planner.PowerUp` (locally curated).
- **Detail pages use stable keys** so links survive a re-sync: routes at `routes:detail` keyed by `name_hash` (`/routes/r/<name_hash>/`), segments at `routes:segment_detail` keyed by the signed 64-bit `segment_id` (`/routes/segments/s/<segment_id>/`).
- **Charts** are framework-free inline SVG (no chart library), ported from Zwift Speed Lab: `apps/zwift_data/static/zwift_data/profile_chart.js` renders the grade-coloured elevation profile (+ segment bands, lead-in shading, hover crosshair) and the VeloViewer-style route map; `route_detail.js` fetches the data and draws it; `chart.css` scopes a fixed-dark palette under `.zsl-chart` (the SVG grid colours are hardcoded, so it stays legible in any DaisyUI theme). Chart data is lazy-fetched from `routes:profile_json` / `routes:route_segments_json` (both `team_member`-gated JSON endpoints).
- **Route detail** renders the vELO2 factor bars directly from `ZwiftRoute.velo_factor_bars()` when weights have been imported.
- **Admin buttons** (both `racing_admin`): "Check for updates" (`routes:check_updates`) enqueues the `sync_zwift_data` task (guarded by `ZwiftDataset.syncing`); "Load vELO weights" (`routes:load_velo`) imports the bundled ZwiftRacing JSON onto `ZwiftRoute` by `name_hash`.
- **GPX upload was removed** — the canonical dataset supplies profiles, so the old `RouteGpx` model, `services/gpx.py`, the `gpxpy` dependency, and the upload/delete views/URLs are gone (migration `ttt_planner/0021_delete_routegpx`).

## Analytics

Client-side JS in `base.html` sends page data to `/api/analytics/track/` (Django Ninja). `PageVisit` model stores combined server+client data. Dashboard at `/analytics/` (requires `app_admin`). Key files in `apps/analytics/`.

## Notification Badges

Sidebar/avatar badges are driven by context processors with short per-user caches. Source files: `apps/team/context_processors.py`, `apps/events/context_processors.py`. Both are registered in `TEMPLATES["OPTIONS"]["context_processors"]` in `gotta_bike_platform/settings.py`.

- `pending_verification_count` (team) — count of `RaceReadyRecord.status=PENDING` the current user can review (mirrors same-gender gate from `verification_records_view`). Sidebar badge on "Verification Records".
- `pending_availability_count` (events) — count of published `AvailabilityGrid` rows in the user's squads with no response yet. Drives the warning dot on the avatar and the count next to "My Events" in the user-menu dropdown.

Both gate on permission/auth before any DB call, then cache the count for 60 s per user. New badges should follow this pattern (skip the query when the user can't act on it; cache short).

## Strava Integration

`apps/club_strava/` - Strava club activity sync. Token refresh is automatic on 401 (tokens saved to Constance). Activity list at `/strava/`, manual sync at `/strava/sync/`. Constance settings: `STRAVA_CLUB_ID`, `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_ACCESS_TOKEN`, `STRAVA_REFRESH_TOKEN` (tokens auto-updated).

## Testing

Tests use **pytest + pytest-django**. Config lives in `[tool.pytest.ini_options]` in `pyproject.toml`; shared fixtures in the top-level `conftest.py`.

- Discovery: `tests.py`, `test_*.py`, `*_tests.py` under `apps/` and `gotta_bike_platform/`.
- Test DB is built from current model state via `--no-migrations` (migrations are **not** replayed). `--reuse-db` keeps the test DB between runs — pass `--create-db` after a model change to force regeneration.
- Why `--no-migrations`: a latent bug in `apps/accounts/migrations/0013_add_is_race_ready_cached_field.py` makes fresh migrations fail (the data migration imports the live `User` model, which selects columns added in 0017). Production missed this because 0013 ran before 0017 was created. Tracked in TODO.md P0; remove `--no-migrations` once fixed.
- **Gotcha — a green test run does not mean your local dev DB is migrated.** Because tests build their schema from model state (`--no-migrations`), a new migration you just created can pass every test while the running dev server still errors with `OperationalError: no such column ...`. After `makemigrations`, always run `uv run python manage.py migrate` before hitting the app locally. (Production/Railway applies migrations on deploy, so this is a local-only trap.)

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

- **Primary**: `sync_guild_members` background task (`apps/accounts/tasks.py`) calls Discord's REST API directly via `apps/accounts/services.py:fetch_guild_members_from_discord` (paginated, 429-aware). Scheduled by the in-process APScheduler — cadence is `SCHEDULER_SYNC_GUILD_MEMBERS_HOURS` Constance setting (default 6h). Also triggerable manually from `/site/config/background_tasks/`.
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
- `refresh_all_race_ready` scheduled task — periodic full sweep, also handles expiration

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
   - `EXPIRE_WARNING_DAYS` (JSON list of int days, default `[15, 7, 3, 1]`) — the `warn_expiring_verifications` scheduled task DMs each record's owner at most once per calendar day, enforced by `RaceReadyRecord.last_warned_at`. Each DM also includes the user's other verified records and their days-remaining. Lives in `gotta_bike_platform/task_registry.py`; also manually triggerable from `/site/config/background_tasks/`.
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
