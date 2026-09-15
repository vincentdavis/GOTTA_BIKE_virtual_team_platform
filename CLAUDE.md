# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django 6.1 application for The Coalition Zwift racing team. Integrates with ZwiftPower and Zwift Racing APIs to manage
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
uv run python manage.py ensuresuperuser        # Idempotent bootstrap: no-op if a superuser exists, otherwise creates one from SUPERUSER_USERNAME / SUPERUSER_PASSWORD / SUPERUSER_EMAIL env vars. Not run on deploy (commented out in entrypoint.sh) — run by hand

# Background Tasks (django.tasks API; DB backend + db_worker from django-tasks-db)
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

# Production = entrypoint.sh (Dockerfile CMD): migrate, seed, db_worker &, scheduler &, then Granian.
# --host :: for Railway's IPv6 private network; workers x blocking-threads = the DB connection budget
uv run granian gotta_bike_platform.wsgi:application --interface wsgi --host :: --port "${PORT:-8000}" --workers "${WEB_WORKERS:-2}" --blocking-threads "${WEB_BLOCKING_THREADS:-2}"
```

## Architecture

### Configuration

- `gotta_bike_platform/config.py` - pydantic-settings for environment variables (loaded from `.env`)
- `gotta_bike_platform/settings.py` - Django settings, imports config values from `config.py`
- Required env vars: `SECRET_KEY`, `DATABASE_URL` (defaults exist for local dev only — must be set in production)
- Optional env vars: `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` (OAuth)
- Optional env vars: `LOGFIRE_TOKEN`, `LOGFIRE_ENVIRONMENT` (observability)
- Optional env vars: `SUPERUSER_USERNAME`, `SUPERUSER_PASSWORD`, `SUPERUSER_EMAIL` (read only by a hand-run `manage.py ensuresuperuser`)
- Runtime settings (via constance): API credentials and team settings (see Dynamic Settings below). **Note**: code that does `from constance import config` (e.g. `config.DISCORD_BOT_TOKEN`, `config.GUILD_ID`) reads from constance, *not* from `gotta_bike_platform/config.py` — the two `config` objects are unrelated.

### Static Files & Storage

- **Static files**: WhiteNoise (compressed, cached, served from memory). `collectstatic` writes to `staticfiles/`.
- **Media files**: S3-compatible storage when configured (Railway), otherwise local filesystem.
- S3 env vars (optional): `AWS_S3_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_REGION_NAME`
- **Verification media: never render `media_file.url`** (template, admin, API). On S3 every `FieldFile.url` is a 15-min presigned URL that can be forwarded and not revoked. Link `team:verification_record_media`, which re-checks `can_view_verification_media` on every request (404 on deny, `Cache-Control: no-store`) before minting a short-lived URL; `RaceReadyRecordAdmin` excludes `media_file` for the same reason

### Apps (in `apps/`)

Read each app's `models.py` for full field lists. Bullets below capture purpose + cross-app interactions + non-obvious behavior only.

- **Account deletion** — `apps/accounts/services.py:delete_user_account(user, *, deleted_by)` is the single implementation, used by the rider's own `/user/profile/delete/` page and the admin Compliance tool. It purges verification media *before* the cascade (Django's Collector bulk-deletes and never calls `Model.delete()`, so nothing on the model can hook it), calls zauth `disconnect()`, and deletes the `MembershipApplication` (keyed by `discord_id`, no FK) and `GuildMember` rows the cascade cannot reach. The "what is deleted / what we keep" copy is one partial, `accounts/partials/_deletion_effects.html`, shared by both screens. **Note the `GuildMember` row returns on the next guild sync if the person is still in the Discord server** — deleting the account does not remove them from Discord
- `accounts` - Custom User model (Discord/Zwift fields), django-allauth adapters, role-based permissions. Key entry points: `decorators.py` (`discord_permission_required`, `team_member_required`), `GuildMember` (Discord member tracking — see Guild Member Sync), `YouTubeVideo` (RSS-fetched videos for Team Feed). **`User.save()` clears `youtube_channel_id` and deletes that rider's `YouTubeVideo` rows whenever `youtube_channel` changes or is blanked** — the ID is scraped once by `sync_youtube_channel_ids` (which only looks at riders with a URL and no ID) and never re-checked, so a stale one feeds the old channel's videos to the profile, the Team Feed and `/api/dbot/recent_videos` forever. It lives on the model so the profile form, the admin and the shell are all covered; a save that does not write the URL (`update_fields` without it) is left alone, and an explicitly edited `youtube_channel_id` wins over the clear
- **Team kit is two fields, and only one of them is current.** `User.team_kit` is the live one: a per-kit `{slug: KitStatus}` map with five statuses, written from the rider's profile form, the admin's "Team kit" section and the bulk CSV import at `/site/config/team_kit/` (`apps/team/kit_csv.py`). `User.has_jersey` is the legacy boolean it supersedes; the two have **never been reconciled**, so don't treat either as authoritative for the other. Its only writer is the admin changelist (`list_editable`) — a deliberate rescue, because the bulk CSV on `/team/membership-review/` was its sole write path anywhere and that page is on its way out, while three surfaces still read it: the `/team/discord-review/` filter, that page's CSV export, and a `data_connection` Sheets field. Retiring the field means removing those three readers and checking for saved Sheets configs that already select it. Guarded by `apps/accounts/test_has_jersey_writable.py`
- `team` - Core team management. Models: `RaceReadyRecord` (see Race Ready Verification), `TeamLink`, `RosterFilter` (**5-min expiration**), `MembershipApplication` (see Membership Registration), `DiscordRole` / `DiscordChannel` (synced from server, used as Select dropdown choices in Event/Squad forms). Services: `get_unified_team_roster()` merges ZP + ZR + User data; `get_user_verification_types(user)` returns required verification types per ZP category
- `zwift` - Zwift integration, entirely OAuth-based. `client.py` talks to the private zauth microservice (connect / disconnect / status / racing profile), `verification.py` reconciles `zwid_verified` from it, `profile_fields.py` fills blank country/gender from the Zwift profile. **The legacy Sauce-mod password flow is gone** — `utils.fetch_zwift_id` sent a rider's Zwift email and password as URL query parameters to a third party, and one caller was the *unauthenticated* public registration form. Do not reintroduce a credential-based path; `manual_zwift_verify` is the admin-reviewed fallback for riders who cannot use OAuth. **The zauth connection wins:** `verification.reconcile_all` (hourly) and every `/user/zauth/` visit re-grant `zwid_verified` to anyone the service reports connected, so un-verifying a rider needs `apps.zwift.client.disconnect(str(user.pk))` first. The admin reject does this; the profile's "Remove" (`accounts.views.unverify_zwift`) does not, so it doesn't stick for a connected rider. **`zwid_verified` is bound to the `zwid` it verified** — every writer of one writes the other in the same save, so the two can never be observed out of step: changing the ZWID in `manual_zwift_verify` clears the verification, and `unverify_zwift` clears the provenance too. Both also refresh `is_race_ready` (required types are keyed on zwid → ZP category) and `unverify_zwift` moves the Discord role with it. The two manual forms — the rider's and the *public* registration one — parse through `apps.accounts.utils.parse_zwid_input`, which refuses non-ASCII digits (`"٣".isdigit()` is True and `int()` reads it as 3), values past the 32-bit column, and digit runs `int()` itself won't convert. It returns `(zwid, form, entered)`: **`entered` is the number the rider typed and is logged even when rejected** — a ZWID is an id, and it is what answers "it wouldn't take my ID" — while `form` (`zwiftpower_url`/`digits`/`url`/`empty`/`other`) stands in for an entry carrying no number, since the box takes any text.
- `zwiftpower` - ZwiftPower API integration. Models: `ZPTeamRiders`, `ZPEvent`, `ZPRiderResults`. Client in `zp_client.py` (session-based; logs in by posting the team account's constance `ZWIFT_USERNAME`/`ZWIFT_PASSWORD` to Zwift's SSO form — the one sanctioned credential login, unrelated to zauth)
- `zwiftracing` - Zwift Racing API integration. `ZRRider` stores per-discipline `seed_*` and `velo_*` rating fields. Client in `zr_client.py` returns `(status_code, json)` tuples; 429s return data with `retryAfter` instead of raising
- `analytics` - Server-side page-visit tracking enriched by a client-side JS snippet in `base.html`. Dashboard at `/analytics/` (`app_admin` only). Tracking endpoint: `POST /api/analytics/track/` (Django Ninja)
- `club_strava` - Strava club activity sync. See Strava Integration section
- `dbot_api` - Discord bot REST API using Django Ninja (see Discord Bot API section). The task registry it used to host has moved to `gotta_bike_platform/task_registry.py`.
- `data_connection` - Configurable Google Sheets exports via service account. Field selection across User/ZP/ZR, filters by gender/division/rating/phenotype. **Manual sync clears the sheet and rewrites all data**
- `events` - Event management with squads, signups, availability grids, scheduled races, and Discord thread integration. See Event Permission Gates below for the load-bearing behavior
- `magic_links` - Single-use passwordless login links (5 min). Still live: `GET /api/dbot/team_links` mints one for the bot's `/team_links` command, so `/m/` must stay; don't extend it to new flows
- `user_api` - Per-user API keys with bearer auth (Django Ninja). `UserApiKey`: 30-day default expiry, hashed at rest, scoped to one user. `purge_expired_api_keys` scheduled task hard-deletes keys expired > 90 days
- `tickets` - **Internal only** (sidebar link intentionally disabled). Member-support / team-management ticket queue. Non-obvious: `Ticket.closed_at` is auto-managed by `save()` on status transitions to/from `closed`; `apps/tickets/services.py:create_member_left_ticket` fires from the guild-member sync when `date_left` is freshly stamped (idempotent while a non-closed ticket exists for that `GuildMember`). Gated by `team_member_required`, then scoped by `apps/tickets/views.py:visible_tickets` — `ticket_admin` sees the whole queue, everyone else only tickets they submitted or are assigned. **System-generated tickets have `submitted_by=None`, so they belong to nobody and are admin-only.** Out-of-scope tickets 404 rather than 403, so ids are not confirmed
- `cms` - Dynamic CMS pages (`Page` model) with markdown body, draft/published workflow, sidebar/user-menu placement (`nav_location` = `main_nav` or `user_menu`), per-page `require_login` / `require_team_member`. Context processor exposes `cms_nav_pages` + `cms_user_menu_pages`
- `zwift_data` - **Canonical, single source of truth** for Zwift worlds/routes/segments, synced from the [Zwift Speed Lab](https://zwiftspeedlab.coalitionracing.com) `/api/data/all.zip` bundle. Models `ZwiftWorld` / `ZwiftRoute` / `ZwiftSegment` + a `ZwiftDataset` version singleton. **The planners FK straight to `ZwiftRoute`** (`ttt_planner.TttPlan.route`, `ladder_planner.LadderMatchup.route`). `ZwiftRoute` also carries **curated** fields not in the dataset — the ZwiftRacing vELO2 Race weights (`velo_sprint/punch/climb/endurance/pursuit` as percent, + `velo_num_events`) plus `recommended_laps` / `supports_laps` — with `VELO_FACTOR_META` / `has_velo_factors` / `velo_factor_bars()` (same API the ladder `compute.py` uses). `services/sync.py:sync_dataset()` downloads the bundle, stores `routes.json` / `segments.json` / `route_profiles.json` in object storage (bucket) under `zwift_data/`. **Route sync upserts by `name_hash` (writing only `ZwiftRoute.SYNCED_FIELDS`) so curated vELO/laps survive a re-sync and row PKs stay stable for the FKs**; worlds/segments are delete-and-recreate. `catalog.py` serves the bulk geometry (per-route elevation/GPS profile, route↔segment crossings) from storage via a **synced_at-stamped in-process cache** (reloads only when a newer sync lands — safe across web workers). `services/velo.py` imports the ZwiftRacing routes JSON (`apps/zwiftracing/docs/ZwiftRacing Routes VELO WEIGHTS.json`) joining by `routeId == name_hash` — via `manage.py import_velo_weights` or the "Load vELO weights" button (`routes:load_velo`, `racing_admin`). Scheduled weekly (`SCHEDULER_SYNC_ZWIFT_DATA_HOURS`); manual seed via `manage.py sync_zwift_data`. Source of truth for the `/routes/` reference page — see Routes Page below
- `ttt_planner` - TTT planner + the shared `/routes/` reference page. Owns `TttPlan` / `PlanRider` / `PowerUp` (power-ups are locally curated). **`Route`/`Segment` were retired** (migration `ttt_planner/0022`) — routes/segments now come entirely from `apps.zwift_data`; the planner route pickers (`terrain.route_options()`, `ladder_planner.services.courses.route_options()`) read `ZwiftRoute` cycling routes. `worlds.py` + `data/*.json` remain only because historical migrations import them

#### Event Permission Gates (`apps/events/views.py`)

Read these helpers before touching event/squad views — most non-trivial behavior in the events app routes through them.

- `_can_manage_event_squads(user, event)` — gates squad create/delete, set captain/vice-captain, "remove from all squads" and the channel-access audit. Event admins, superusers, and holders of the event's `head_captain_role_id` **or any of its `coordinator_role_ids`** (`_is_event_coordinator`). Squad **edit** is gated by `_can_manage_squad_availability`, so a squad's own captains can edit it
- `_can_manage_squad_availability(user, squad)` — gates squad edit, availability grids, scheduled races, and Discord thread actions. Adds squad captain/vice-captain and squad `discord_captain_role` holders to the set above
- **Channel access audit** — `/events/<id>/squads/<sid>/channel-access/` (gated `_can_manage_event_squads`, read-only, HTMX partial from the Manage Squads panel). Reads the channel's `permission_overwrites` and the guild's roles **live** from Discord, computes VIEW_CHANNEL per guild member using `apps/events/channel_access.py`, and diffs against the squad roster. Answers "who can actually see this channel", which is **not** the same question as "who holds the squad role" — any other role or a member-specific overwrite grants it too. Member→roles comes from the `GuildMember` cache, so that half is only as fresh as the last guild-member sync. Category permissions are deliberately not fetched: Discord implements category inheritance by copying overwrites onto the child channel
- `_can_view_v_report(user, event)` — gates the Eligibility page (`/events/<id>/squads/eligibility/`) **and**, via `can_view_signup_table`, the signup table on the event page. Event admins, superusers, head-captain-role holders, **coordinator-role holders**, and captains/vice-captains of any squad in the event. Coordinators are in because they can already export the full signup CSV, which carries strictly more than either surface — so the two are no longer allowed to disagree. It is the `_can_manage_squad_availability` set minus squad `discord_captain_role` holders, but event-scoped (a captain of any squad sees the whole event)
- **A role that carries power can never be handed to a squad's members.** `SquadForm` refuses the event's `head_captain_role_id` on all four role fields, and refuses its `coordinator_role_ids` and `captain_role_ids` on the two member-facing ones (`team_discord_role`, `region_role`) — those are auto-assigned to riders as they join, so a squad captain, who may edit their own squad, could otherwise point its role at a coordinator role, add themselves and come out an event coordinator. The designation fields (`discord_captain_role`, `regional_coordinator_role`) still take their own roles. The clean methods are the gate (`_refuse_privileged_role`); pruning the pickers is only convenience. Guarded by `apps/events/test_squad_role_guards.py`
- `Squad.discord_captain_role` must be one of the event's `captain_role_ids` (Role Setup page) — same shape as the region and coordinator roles. Migration `events/0069` seeds it from squads' existing captain roles but **only those matching the event's prefixes** — `0068` seeded region roles unfiltered and made Role Setup un-saveable, because `clean()` rejects an off-prefix id while the checkbox list hides anything off-prefix, leaving no way to untick it
- `Squad.region_role` must be one of the event's `region_role_ids` (Role Setup page) — same shape as the coordinator role: server-side re-validated in `clean_region_role`, stale ids dropped from `initial`, picker disabled when the event has none. Migration `events/0068` seeds each event's list from the region roles its squads already used, so tightening the rule did not orphan existing data
- `Squad.regional_coordinator_role` must be one of the event's `coordinator_role_ids` (Role Setup page) — server-side re-validated in `clean_regional_coordinator_role`, and a since-removed id is dropped from `initial` rather than offered back, or the whole squad form would be un-saveable
- Squad Discord roles **must** start with one of the event's `prefixes` (server-side re-validated against `DiscordRole` even if the client tampers with the choices list). The squad-role dropdown is disabled when the event has no prefixes set
- `coordinator_role_ids` ("Regional/Group Coordinators"), `region_role_ids` ("Region Roles") and `captain_role_ids` ("Captain Roles") — multi-selects on Role Setup, all restricted to roles starting with `EVENT_ROLE_PREFIXES` and all validated through `EventRoleSetupForm._clean_prefixed_role_ids`. The template renders the three checkbox lists from one slug-keyed JS helper
- `Squad.region_role` — optional prefix-filtered Discord role auto-**added** when a rider is assigned to the squad and auto-**removed** when they leave, wired into the same `squad_assign_view` add/remove branches and self-join invite flow that manage `team_discord_role` (helpers `_assign_region_role` / `_unassign_region_role_if_unused`). The remove is guarded: `_unassign_region_role_if_unused` keeps the role while the rider is still a `MEMBER` of any **other** squad (across **all** events) whose `region_role` is the same ID — so a region role shared by several squads survives leaving one of them. Like `team_discord_role`, withdraw/signup-delete do **not** strip it (parity, not a bug)
- Role Setup (`/events/<id>/role-setup/`) and Discord Roles (`/events/<id>/discord-roles/`) share `_can_manage_event_roles`: `assign_roles`, event head captain, **or a coordinator role** can edit (so a coordinator can change `head_captain_role_id` / `coordinator_role_ids`); `event_admin` alone gets Role Setup read-only
- "Save & Create Thread" (`_create_slot_thread`) requires `status=confirmed`, riders selected, `squad.discord_channel_id` and no existing thread; the URL lands on `slot.thread_link`. "Save & Post Update" only needs an existing `thread_link` (any status or riders). Both go through `apps/accounts/discord_service.py`. Riders, subs, captain, vice-captain and DSs are in `allowed_user_ids`, so they get pinged even when not racing
- `signup_notification_channel_id` on `Event`: `0` disables per-rider signup notifications
- **Signup requirements** (`apps/events/signup_requirements.py`): `Event.require_complete_profile_signup` (default **on** — `User.is_profile_complete`, which includes Zwift verification) and `Event.require_race_verified_signup` (default off — the cached `is_race_ready`). `signup_blockers(event, user)` is the one rule, and **every way onto an event asks it**: `event_signup_view`; `squad_invite_view` (joining signs the rider up, and would otherwise also re-activate a withdrawn signup); and `add_members_view`, because a captain adding a rider must not bypass it — ineligible riders are skipped and named, and the member search marks them "can't add". A rider already REGISTERED is neither removed nor re-checked (edit still works; an invite only adds a squad place). No superuser exception. The Django admin is deliberately unrestricted. Tests that sign a rider up and are not about these use the `complete_profile` fixture
- **Published sheets are editable; their *shape* is not, once anyone has answered.** `SHAPE_FIELDS` in `apps/events/views.py` (dates, times, `slot_duration`, `grid_timezone`, `single_slot`, `blocked_cells`) is refused by `_changed_shape_fields` when `grid.active_responses().exists()` — only answers from current squad `MEMBER`s count, for the lock and for counts/heatmap/slot picker. Anything counting responders must use `active_responses()`, not `grid.responses` (and keep its two membership conditions in one `filter()`). A response stores UTC `{date, time}` cells with **no FK to a cell**, and the rider's next submit is a wholesale replace — so a shape change orphans answers and the next submit deletes them. Closed sheets are not editable at all. The builder disables the controls, but it posts JSON, so the server check is the real one.
- All grid/response/slot times stored in UTC, converted at render. `EventSignup.signup_timezone` is saved whenever the event has `timezone_options` (`timezone_required` only makes a pick mandatory); `signup_squad_gender` only when `squad_gender_required` is on

#### Custom Signup Questions (`apps/events/signup_questions.py`)

Admins add per-event questions (`SignupQuestion`: `question_type` = text/single/multi/boolean, `options`, `required`, `order`) at `/events/<id>/signup-questions/` (gated `is_event_admin or is_superuser`, same as event edit; linked from the event edit page). Riders answer on the signup + edit-signup modals in `event_detail.html` (fields named `custom_q_<id>`, rendered by the `_signup_question_fields.html` partial); answers are editable after signup and shown in the admin signup table's toggleable **Answers** column. There is **no** Discord-notification or Sheets export of answers.

The question `label` + `help_text` are admin-authored and render **inline markdown** via the `render_markdown_inline` filter (in `accounts_tags.py` — same trust level as event descriptions; it renders markdown then unwraps a lone top-level `<p>` so it sits inline in a form label / table cell). Rider-authored content (choice `options`, answer values) stays auto-escaped — never render those as markdown.

Answers live on `EventSignup.custom_answers` (JSON `{str(question_id): answer}`; text→str, single→str, multi→list, boolean→bool). Load-bearing rules enforced by `signup_questions.py`:
- **Only real answers are stored** — a blank/unchecked answer never writes a key (and clears one on edit). So `SignupQuestion.has_answers` (a `custom_answers__has_key` test) truthfully means "answered", which is what freezes `question_type` (`SignupQuestionForm.clean_question_type`). Don't reintroduce writing empty keys.
- `parse_custom_answers(event, post, existing=...)` **merges** onto the existing dict (orphaned answers to since-deleted questions survive, harmless, just not displayed) and enforces `required` only at submit time. A rider's prior single/multi choice is **grandfathered** (kept selectable + accepted) so an admin removing an in-use option can't make an unrelated signup edit unsaveable.

### Authentication (django-allauth)

- Discord OAuth is the rider login; `/admin/` also accepts username/password (`ModelBackend`) for staff such as the `ensuresuperuser` account
- **allauth's own signup is closed** (`NoLocalSignupAccountAdapter.is_open_for_signup` → False, wired as `ACCOUNT_ADAPTER`), so an account can only come from a Discord login, where the block-list / guild / verified-email checks live. `DiscordSocialAccountAdapter.is_open_for_signup` returns True to keep Discord signup open — allauth's social adapter delegates to the account adapter, so removing it shuts new riders out. Guarded by `apps/accounts/test_local_signup_closed.py`
- **Guild membership required**: Users must be a member of the configured Discord server (`GUILD_ID`) to sign up or log
  in
- Custom User model fields: `discord_id`, `discord_username`, `discord_nickname`, `zwid`,
  social fields (`strava_url`, `youtube_channel`, `youtube_channel_id`, `twitch_channel`, `instagram_url`,
  `facebook_url`, `twitter_url`, `tiktok_url`, `bluesky_url`, `mastodon_url`, `garmin_url`, `tpv_profile_url`),
  equipment fields (`trainer`, `powermeter`, `dual_recording`, `heartrate_monitor`)
- TOTP two-factor authentication via `allauth.mfa`
- Custom adapter at `apps/accounts/adapters.py` verifies guild membership and syncs Discord profile data. Rejected users (not in the guild, blocked, unverified email, Discord API errors) go back to `account_login` with an error message — never to `DISCORD_URL`, which only adds a "Join here" when it is an http(s) URL. See "Discord OAuth Adapter" below for the load-bearing gotchas
- OAuth scopes: `identify`, `email`, `guilds`
- URLs at `/accounts/` (login, logout, 2fa management)

#### Discord OAuth Adapter (`apps/accounts/adapters.py`)

**Critical gotchas:**

- `pre_social_login` reconnects existing users by `discord_id` if SocialAccount was lost — prevents profile data loss
- `pre_social_login` only updates Discord fields, **never** profile fields (`first_name`, `last_name`, `birth_year`, etc.)
- `save_user` is only called for NEW users; `populate_user` runs on **every** Discord callback (on a throwaway `new_user()`, before `lookup()`), so keep it side-effect free
- **Always use `update_fields`** when saving User in adapter code: `user.save(update_fields=['discord_id', ...])` — bare `user.save()` overwrites profile data

### Profile Completion

Users are encouraged to complete their profile but are **not blocked** from accessing the app (events can require it to sign up — see Signup requirements).

#### Profile Fields

Required fields for profile completion:

- `first_name`, `last_name` - User's real name
- `birth_year` - Year of birth (validated: 1900 to current_year - 13)
- `gender` - Gender (male/female/other)
- `timezone` - User's timezone (e.g., "America/New_York")
- `country` - Country of residence (uses `django-countries` CountryField with ISO 2-letter codes, rendered as dropdown)
- `trainer` - Smart trainer type (required for racing)
- `heartrate_monitor` - Heart rate monitor type (required for racing)
- `zwid_verified` - Zwift verification, counted via `User.has_accepted_zwid_verification` (equals `zwid_verified` until Constance `ZAUTH_VERIFICATION_REQUIRED` is on; then only zauth verifications count). Gate or display on that property (`.values()` rows: `apps/team/services.py:verification_accepted`); read the raw field only where the stored fact itself is wanted

Properties: `user.is_profile_complete` (bool), `user.profile_completion_status` (dict of field→bool).
Incomplete profiles show a red warning banner in `base.html` (not blocking, just a warning).

### Public User Profiles

Public profiles at `/user/profile/<user_id>/` (requires `team_member` permission). Your own renders as teammates see it, with an Edit banner (`is_own_profile`) — no redirect.

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
- `performance_verification_team` - `/team/performance-review/`. With `approve_verification` (needed to open `/team/verification/<pk>/` at all) it can also view media on reviewed records, change the status of and delete records of any status, and edit weights; the same-gender rule still applies
- `data_connection` - Access to Google Sheets data exports
- `pages_admin` - Can create and manage CMS pages
- `event_admin` - Create, edit, and manage events, squads, and signups
- `ticket_admin` - See and edit **every** ticket at `/tickets/`; without it a member sees only the tickets they submitted or are assigned (system-generated tickets have no submitter, so they are admin-only)
- `assign_roles` - Manage Discord role setup and assign/unassign Discord roles on events; event Head Captain Role and coordinator-role holders also get this ability per-event

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
- `PERM_TICKET_ADMIN_ROLES` - Role IDs that can see and edit every ticket

#### Usage in Views

- Decorator: `@discord_permission_required("team_captain")` — an authenticated user without it always gets 403 (no redirect — prevents loops); anonymous users always redirect to login. `raise_exception` is accepted but ignored
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

When adding a view with `@discord_permission_required` or `@team_member_required`, also add it to `PERMISSION_REGISTRY` in `apps/accounts/permission_registry.py` (format: `"/path/ - Description"` in the `views` list). This powers the help icons on `/site/config/`, which resolve by stripping `PERM_`/`_ROLES` off the Constance key (`get_permission_help`) — so a permission's key must be exactly `PERM_<NAME>_ROLES`, with a registry entry named `<name>`.

### Background Tasks

Uses Django's built-in tasks API (`from django.tasks import task`) with `django_tasks_db.DatabaseBackend` from the `django-tasks-db` package, which also provides `db_worker` and `prune_db_task_results`. Define with `@task`, enqueue with `.enqueue()`.
**Gotcha**: `run_after` must be a `datetime`, not `timedelta` — use `my_task.using(run_after=timezone.now() + timedelta(seconds=60)).enqueue()`.

### Scheduler (in-process APScheduler)

`gotta_bike_platform/management/commands/scheduler.py` runs a `BlockingScheduler` (in-memory job store, one `IntervalTrigger` per job; an interval ≤ 0 skips the job; starts anchored to midnight UTC, so a job slower than daily re-anchors on every deploy). It is its own process but **not its own service**: in production `entrypoint.sh` backgrounds it and `db_worker` inside the web container. Don't add a separate scheduler service — jobs would enqueue twice (each web replica already runs one). Jobs enqueue Django tasks; `db_worker` executes them.

- **Task registry** — `gotta_bike_platform/task_registry.py:TASK_REGISTRY` is the single source of truth for scheduled and manually-triggerable tasks. The scheduler calls `get_scheduled_tasks()` (filters `scheduled=True`, resolves each `hours_setting` Constance value). The admin "Run Now" UI at `/site/config/background_tasks/` reads the same dict via `_get_task_registry()` in `apps/accounts/views.py`.
- **UI** — `/site/config/scheduler/` (driven by the `Scheduler` group in `CONSTANCE_CONFIG_FIELDSETS`) lets admins adjust the cadences. Interval changes require a scheduler restart, i.e. redeploying the web service.
- **When adding a new scheduled task**: import the task in `task_registry.py`, add an entry with `scheduled=True` and a `hours_setting` pointing at a new `SCHEDULER_*_HOURS` Constance setting, then add that key to the `Scheduler` fieldset. For a task needing finer granularity, declare `minutes_setting` with a `SCHEDULER_*_MINUTES` setting instead — an entry uses exactly one of the two, and `resolve_interval_minutes()` converts hours entries so the scheduler always consumes minutes. For a manual-trigger-only task (no schedule), omit `scheduled` (or set to `False`); no Constance setting needed.

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
- `apps/accounts/discord_service.py` - Direct Discord REST client (httpx, sync). Bot token from `config.DISCORD_BOT_TOKEN`. Functions: `send_discord_dm`, `send_discord_channel_message` (supports `allowed_user_ids` for proper @-mention notifications), `send_verification_notification`, `add_discord_role`, `remove_discord_role`, `sync_user_discord_roles`, `create_discord_thread` (returns `(thread_id, error)`). The Discord bot has no HTTP server, so the platform calls Discord itself — mostly through this module, but `sync_discord_channels`/`sync_discord_roles` (`apps/team/tasks.py`) and `fetch_guild_members_from_discord` make their own bot-token calls.
    - **DM opt-out:** `send_discord_dm` is the only DM gate. For a member with `User.discord_dm_opt_out` it sends nothing and returns **True** (so retry and 'already warned' logic treat it as done) — code that counts or reports sends must check the flag itself, as `warn_expiring_verifications` does. Never open a DM channel outside this function.

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
    - `GET /api/dbot/search_teammates?q=` - Search active team riders by **any** name they are known by (ZwiftPower name, Zwift Racing name, Discord username/nickname, real name); returns `alias` naming the match when it was not the ZwiftPower name
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
- `/m/` — single-use magic login links (see Apps section)
- `/ttt/`, `/ladder/`, `/routes/` — TTT planner, ladder planner, routes reference (see Routes Reference Page)
- `/user/zauth/` — `apps.zwift.urls` (Zwift OAuth connect/disconnect)
- `/healthz` — public JSON status/version probe

Non-obvious gates / behavior not visible from the URL pattern alone:

- `/team/roster/f/<uuid>/` — filtered roster, **5-min expiration**
- `/team/apply/<uuid>/` — public (no auth) membership registration form
- `/events/<id>/squads/add/` and `/<sid>/delete/` — `_can_manage_event_squads` (event admin / superuser / head captain or coordinator role). `/squads/manage/` — `_can_view_squad_manage` (those plus any squad's captain/vice-captain/`discord_captain_role` holder; per-squad controls are re-gated). `/<sid>/edit/` — `_can_manage_squad_availability`
- `/events/<id>/squads/<sid>/availability/...` — `_can_manage_squad_availability` (above + squad captain/vice-captain + `discord_captain_role` holders)
- `/events/<id>/role-setup/` — event admins read-only; `assign_roles`, head captain or a coordinator role can edit
- `/events/<id>/discord-roles/` — `assign_roles`, event head captain, or an event coordinator role
- `/events/<id>/signups/export/` — signup CSV; **narrower than the signup table**: event head captain or coordinator role only (plus superusers). Not `event_admin`
- `/analytics/` — `app_admin` only
- `/robots.txt` — dynamic (rendered by `gotta_bike_platform/views.py`)
- `/accounts/3rdparty/signup/` — shadowed by `block_social_signup` (redirects to login), ahead of the allauth include

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
- **Responsive tables (mobile)**: `theme/static/css/responsive-tables.css` (loaded once in `base.html`, plain CSS — no Tailwind rebuild) holds the shared mobile-table patterns. **Pattern A** — wide *matrix* tables (toggle grids like Discord Roles, availability grids): add `table-pin-col` to a `<table>` inside an `overflow-x-auto` wrapper to pin the first column while the rest scrolls horizontally (override `--pin-col-bg` if the table doesn't sit on `base-200`). First live use: `templates/events/discord_roles.html`. Add `table-pin-col-2` alongside it to pin a **second** column as well — the second column's left offset is the first column's rendered width, which CSS cannot know, so the template must measure it and set `--pin-col-2-left` on the `<table>` (see the script in `discord_roles.html`). **Pattern B** — wide *data* tables: add `table-cards` to the `<table>` and `data-label="Column"` to every body `<td>` except the first (the card title); below `md` each row renders as a card. Helpers: `mobile-hide`, `cell-title`, `cell-block`, `cell-actions`. Used by the roster and about eleven more; the event signup tables use Pattern A
- **Accessibility base**: `theme/static/css/a11y.css` (loaded after the bundle) forces the focus ring, honours reduced motion and holds the text-size steps; `base.html` has the skip link and the `theme`/`textSize` localStorage keys

#### Mobile & Accessibility Review (standing rule)

**Always, unasked**, after any change that renders UI — templates and partials (HTMX responses too), `theme/static/` or app static CSS/JS, Python that emits markup; not `templates/admin/` — review the affected pages for mobile and accessibility. Depth scales with the change: a label tweak gets a glance, a new page or component the full pass. Target WCAG 2.2 AA; the known-issue backlog is `docs/accessibility.md` (verify an entry before citing it — its checkboxes lag the code).

Check at 375px (320 for reflow), 768 and ≥1024, in **both** themes:
- **Width** — the page scroller is `.drawer-content`, not `body`; nothing may overflow it except inside `overflow-x-auto`. Wide tables use Pattern A/B above; nothing may hide under the fixed bottom tab bar (<640px).
- **Touch & hover** — targets ≥24px (`btn-xs` is exactly that — never shrink it). Phones have no hover: nothing essential only in `title`, `data-tip` or `dropdown-hover`; copy the rider card's click popover (`templates/shared/_user_tooltip_script.html`).
- **Keyboard & focus** — Tab reaches everything, Enter/Space operates it; no `onclick` on `td`/`th`/`div`; never `outline-none`. An HTMX swap that removes the focused control must move focus or announce the result (`role="status"`).
- **Dialogs** — `<dialog class="modal">` + `showModal()` (never the `modal-open` class), `aria-labelledby` its heading, Escape closes, focus returns. Model: `kit-add-dialog` in `templates/accounts/partials/config_team_kit.html`.
- **Names** — icon-only controls need `aria-label`; inputs need a real `<label>` or `for`/`id` (a sibling `label-text` span labels nothing).
- **Contrast** — 4.5:1 text, 3:1 UI, never colour alone. Colour-as-text on a base surface fails: `text-{error,warning,success,info}` and `badge-outline`/`badge-soft` in LIGHT, `text-primary`/`link-primary` in DARK, `text-base-content/50` and fainter. Solid badges/buttons with their `*-content` pass, except `badge-secondary` (both themes) and `btn-primary`/`badge-primary` in dark.
- **Preferences** — at the largest text size nothing clips at 375px; JS smooth-scrolling must respect `prefers-reduced-motion`.

**How** — Browser pane: `preview_start` `dev` (port 8010); sign in locally with `MagicLink.create_for_user(user, "/path/")`, then open `/m/<token>/`. `resize_window` `mobile`/`tablet` (reset to `desktop` after); switch themes with `javascript_tool` `setTheme('dark')` — a saved theme overrides `colorScheme` emulation. `read_page` `filter: "interactive"` finds unnamed controls; Tab plus `document.activeElement` walks focus order. Run `manage.py tailwind build` before judging visuals: the committed `theme/static/css/dist/styles.css` is stale (a class missing from it proves nothing), and the build rewrites that tracked file — restore it unless a rebuild is intended. Static guards: `uv run pytest apps/accounts -k "a11y or text_size or skip_link"`.

**Report** — end with a short "Mobile/a11y" note: the widths, themes and checks covered, and what couldn't be checked. Fix what your own change introduced; for anything else, **alert the user** with file:line, who it affects, the WCAG criterion and a concrete fix (cite `docs/accessibility.md` when it's already listed). Never silently restyle outside the task.

#### daisyUI Blueprint MCP

DaisyUI Blueprint MCP is configured. Use its tools (`generate_page`, `generate_section`, `generate_component`) when creating/modifying DaisyUI templates. Adapt generated code to Django template tags and follow patterns in `theme/templates/`.

### Admin Customization

Custom admin buttons are added via:

1. Override `get_urls()` to add custom URL path
2. Add view method that enqueues task and redirects
3. Create template extending `admin/change_list.html` with button in `object-tools-items` block

### Dynamic Settings (django-constance)

Runtime-configurable settings stored in database, editable via Django admin at `/admin/constance/config/`.

Groups are the `CONSTANCE_CONFIG_FIELDSETS` keys; each is a page at `/site/config/<name lowercased, spaces→_>/` — 20 in all, e.g. Site Settings, Discord Guild (guild/channel/role IDs, bot token, `DBOT_AUTH_KEY`, `EVENT_ROLE_PREFIXES`, new-arrival messages with `{member}`/`{server}`), Zwift Credentials, Permission Mappings (`PERM_*_ROLES` — JSON arrays of Discord role IDs), Verification Settings (`CATEGORY_REQUIREMENTS`, `*_DAYS`, `VERIFICATION_FORM_MESSAGE`, `MAX_MEDIA_UPLOAD_MB` — default 150, read at call time), Events, Strava, Scheduler, Compliance. A new key goes in `CONSTANCE_CONFIG` **and in exactly one fieldset** — in none it never renders (and trips `constance.E001`), in two it fails `test_no_setting_appears_in_two_fieldsets`. Compliance holds the policy URLs and the retention windows (`ANALYTICS_ANONYMISE_DAYS`, `ANALYTICS_DELETE_DAYS`, `RIDER_PROFILE_MAX_DAYS`); a new window belongs there and must be added to `MOVED` in `apps/accounts/test_config_compliance_settings.py`. Two older windows were never moved: `STRAVA_ACTIVITY_MAX_DAYS` (Strava) and `VERIFICATION_MEDIA_MAX_DAYS` (Verification Settings).

**Note**: the `Compliance` fieldset has no page of its own — `/site/config/compliance/` is special-cased in `config_section_page`, and its settings form is rendered by `templates/accounts/partials/config_compliance.html` below the erasure and blocked-login tools, so the retention windows sit with the tools acting on the same data.

Usage: `from constance import config; config.SETTING_NAME`. Add new settings in `settings.py` under `CONSTANCE_CONFIG`.

### Site Image Settings

`SiteSettings` singleton model (`gotta_bike_platform/models.py`) stores `site_logo`, `favicon`, `hero_image`, `not_verified_emoji`, `verified_emoji`, `extra_verified_emoji` (separate from Constance because they're file uploads). Access via `SiteSettings.get_settings()` or `site_settings` template context variable.

`LOGO_DISPLAY_MODE` Constance setting: `name_only` (default), `logo_only`, `logo_and_name`. Falls back to team name if no logo uploaded. The mode is edited under Site Settings; the images under "Site Images".

## Home Page Logic

Home page (`gotta_bike_platform/views.py: home()`): logged-in users get `HOME_PAGE_SLUG_AUTHENTICATED`, or `HOME_PAGE_SLUG` if that is blank; anonymous users get `HOME_PAGE_SLUG`. An empty slug or a missing/unpublished page falls back to `templates/index.html`.

## Routes Reference Page (`/routes/`)

Served by `apps.ttt_planner` views but driven by the canonical `zwift_data` dataset (see the `zwift_data` app bullet). Mounted via `apps/ttt_planner/routes_urls.py` (namespace `routes:`).

- **Tabs**: Routes / Worlds / Segments come from `ZwiftRoute` / `ZwiftWorld` / `ZwiftSegment`; Power-ups stays `ttt_planner.PowerUp` (locally curated).
- **Detail pages use stable keys** so links survive a re-sync: routes at `routes:detail` keyed by `name_hash` (`/routes/r/<name_hash>/`), segments at `routes:segment_detail` keyed by the signed 64-bit `segment_id` (`/routes/segments/s/<segment_id>/`).
- **Charts** are framework-free inline SVG (no chart library), ported from Zwift Speed Lab: `apps/zwift_data/static/zwift_data/profile_chart.js` renders the grade-coloured elevation profile (+ segment bands, lead-in shading, hover crosshair) and the VeloViewer-style route map; `route_detail.js` fetches the data and draws it; `chart.css` scopes a fixed-dark palette under `.zsl-chart` (hardcoded SVG colours; not yet legible in every theme — see `docs/accessibility.md`). Chart data is lazy-fetched from `routes:profile_json` / `routes:route_segments_json` (both `team_member`-gated JSON endpoints).
- **Route detail** renders the vELO2 factor bars directly from `ZwiftRoute.velo_factor_bars()` when weights have been imported.
- **Admin buttons** (both `racing_admin`): "Check for updates" (`routes:check_updates`) enqueues the `sync_zwift_data` task (guarded by `ZwiftDataset.syncing`); "Load vELO weights" (`routes:load_velo`) imports the bundled ZwiftRacing JSON onto `ZwiftRoute` by `name_hash`.
- **GPX upload was removed** — the canonical dataset supplies profiles, so the old `RouteGpx` model, `services/gpx.py`, the `gpxpy` dependency, and the upload/delete views/URLs are gone (migration `ttt_planner/0021_delete_routegpx`).

## Analytics

Client-side JS in `base.html` sends page data to `/api/analytics/track/` (Django Ninja). `PageVisit` model stores combined server+client data. Dashboard at `/analytics/` (requires `app_admin`). Key files in `apps/analytics/`.

## Notification Badges

Sidebar/avatar badges are driven by context processors with short per-user caches. Source files: `apps/team/context_processors.py`, `apps/events/context_processors.py`. Both are registered in `TEMPLATES["OPTIONS"]["context_processors"]` in `gotta_bike_platform/settings.py`.

- `pending_verification_count` (team) — count of `RaceReadyRecord.status=PENDING` the current user can review (mirrors same-gender gate from `verification_records_view`). Sidebar badge on "Verification Records".
- `pending_availability_count` (events) — published `AvailabilityGrid`s in squads where the user is an active `MEMBER`, unanswered and not yet ended (`end_date` ≥ the user's local today, as on My Events). Drives the warning dot on the avatar and the count next to "My Events" in the user-menu dropdown.

Both gate on permission/auth before any DB call, then cache the count for 60 s per user. New badges should follow this pattern (skip the query when the user can't act on it; cache short). Counts are never invalidated on write (TTL only); bump the key's `:vN` when a value's meaning changes. **Tests:** the default cache is a process-wide `LocMemCache` that pytest-django doesn't reset, and SQLite reuses rolled-back PKs, so a cached per-user value leaks into the next test's user — `cache.clear()` in a fixture (see `apps/team/test_expiring_verifications_context.py`).

## Strava Integration

`apps/club_strava/` - Strava club activity sync. Token refresh is automatic on 401 (tokens saved to Constance). Activity list at `/strava/`, manual sync at `/strava/sync/`. Constance settings: `STRAVA_CLUB_ID`, `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_ACCESS_TOKEN`, `STRAVA_REFRESH_TOKEN` (tokens auto-updated).

## Testing

Tests use **pytest + pytest-django**. Config lives in `[tool.pytest.ini_options]` in `pyproject.toml`; shared fixtures in the top-level `conftest.py`.

- Discovery: `tests.py`, `test_*.py`, `*_tests.py` under `apps/` and `gotta_bike_platform/`.
- Test DB is built from current model state via `--no-migrations` (migrations are **not** replayed). `--reuse-db` only matters on a Postgres `DATABASE_URL` (pass `--create-db` after a model change); the default SQLite test DB is in-memory and rebuilt every run.
- Why `--no-migrations`: it began as a workaround for `accounts/0013`/`0014` importing the live `User` model, fixed in `d5c3653` (`uv run pytest --migrations` now passes). It now only saves the replay time — and means the suite never runs migrations, hence the gotcha below. (The `pyproject.toml` comment and the TODO.md P0 item are stale.)
- **Gotcha — a green test run does not mean your local dev DB is migrated.** Because tests build their schema from model state (`--no-migrations`), a new migration you just created can pass every test while the running dev server still errors with `OperationalError: no such column ...`. After `makemigrations`, always run `uv run python manage.py migrate` before hitting the app locally. (Production/Railway applies migrations on deploy, so this is a local-only trap.)

### Shared fixtures (`conftest.py`)

All user fixtures depend on `db` and grant permissions via `User.permission_overrides`, so tests don't depend on Constance or Discord roles.

- `user_model` — the active User class
- `user` — plain user, no permissions
- `team_member` — `team_member` permission
- `app_admin` — `app_admin` + `team_member`
- `event_admin` — `event_admin` + `team_member`
- `superuser` — `is_superuser=True` (bypasses all checks)
- `membership_admin` — `membership_admin` + `team_member`
- `zp_team_rider_factory(zwid=, div=20, divw=0)` — a `ZPTeamRiders` row, for ZP-category variants
- `verification_factory(user, verify_type, status=, days_ago=, ...)` — a `RaceReadyRecord`
- `complete_profile` — a function, `complete_profile(user)`, filling every field `User.is_profile_complete` needs (zauth-verified, so it holds under the cutover flag). Events require a complete profile to sign up by default, so signup tests use it
- `auth_client` — `pytest-django`'s `client` force-logged-in as `team_member`
- `admin_authed_client` — `client` force-logged-in as `app_admin`

Add new permission fixtures in `conftest.py` following the same pattern (`_make_user(..., permissions={...})`). Use feature-local `conftest.py` files for app-specific fixtures (e.g. `healthy_sync` in `apps/rider_data/conftest.py`).

### Writing tests

- Tag DB-touching tests with `@pytest.mark.django_db` (built-in `db` fixture also works).
- Per-file ruff ignores: `**/tests.py` / `**/test_*.py` allow `assert`, missing module/function docstrings and naive datetimes, but **not** `DOC201`; `conftest.py` allows the docstring rules and `DOC201` but **not** `assert` (use `pytest.fail()`).
- For new tests, prefer the shared fixtures over rolling your own User in each test. If you need a variant (different gender, ZP category, etc.) build it from a fixture rather than calling `create_user` directly.
- Avoid making real HTTP calls — patch `httpx` clients (`apps/zwiftpower/zp_client.py`, `apps/zwiftracing/zr_client.py`, `apps/accounts/discord_service.py`, etc.) at the client boundary.

### Running

```bash
uv run pytest                               # full suite
uv run pytest apps/events                   # one app
uv run pytest apps/accounts/tests.py::test_app_admin_has_app_admin_permission
uv run pytest -k permission                 # by keyword
uv run pytest --create-db                   # rebuild a Postgres test DB (SQLite's is in-memory)
```

## Code Style

Ruff configuration in `ruff.toml`:

- Python 3.14 target
- Line length: 120
- Enforces: Django (DJ), security (S), docstrings (D), isort (I), bugbear (B), and more
- Docstrings required (Google style, D212 format)

New Django apps should be created in `apps/` with config name `apps.<appname>`. Every model needs a retention policy — see Personal Data & GDPR.

When handling API responses that may contain `None` values for string fields, use `value or ""` pattern (not
`.get("key", "")` which returns `None` if key exists with `None` value).

## Personal Data & GDPR

The repo is **public** — never commit real rider data. Sensitive here: weight, height, power, heart rate, verification media, birth year, emergency contacts — and much of it concerns people who never signed up (ZwiftPower/ZwiftRacing rows and their `Historical*` shadows, `RiderProfile`).

- **Every new model declares `retention = RetentionPolicy.keep|cascade|strip|delete("<why, over 30 chars>")`** (`gotta_bike_platform/retention.py`; `strip`/`delete` also take `anchor=` and a Constance `setting=`). `gotta_bike_platform/test_retention_policy.py` fails otherwise — it lives outside `apps/`, so `pytest apps/<app>` won't catch it — and its `UNCLASSIFIED` list may only shrink. A timed policy's task must be `scheduled=True` in `task_registry.py` (nothing tests that).
- **Retention windows** are Constance `*_DAYS` settings in the `Compliance` fieldset (see Dynamic Settings).
- **New personal data must be erasable**: `CASCADE` from `User`, handled in `apps/accounts/services.py:delete_user_account` (anything keyed by `discord_id`/`zwid`, or behind `SET_NULL`), or listed as kept in `_deletion_effects.html`.
- **Minimise what leaves the database.** Logfire gets ids (`user_id`, `discord_id`, `zwid`) — never names, email, birth year, measurements or free text. Discord posts and task kwargs carry ids, labels and links, not values. No new third-party script, pixel or CDN in `base.html` without consent handling.
- **Exports**: gate no wider than the page showing those rows, log `user_id` and the row count, and pass rider-chosen text through `gotta_bike_platform/csv_utils.csv_safe`; a new personal column re-opens the question of who may export. Public profiles: see Public User Profiles.
- **Standing instruction:** whenever you touch personal data, look for GDPR improvements — retention, erasure gaps, over-logging, over-broad exports, third-party sharing, consent, transparency, subject access. **Report** each to the user with file:line evidence and a suggested fix rather than silently implementing it: deletions and sweeps are one-way, and several are open policy decisions. (TODO.md "Privacy & Data Protection" tracks some, but is partly stale.)

## Observability (Logfire)

The application uses [Logfire](https://logfire.pydantic.dev/) for observability, logging, and monitoring.

Configured in `settings.py` (top: `logfire.configure()`, end: `instrument_django()`/`instrument_httpx()`).
Env vars: `LOGFIRE_TOKEN` (optional), `LOGFIRE_ENVIRONMENT`. Usage: `import logfire` then `logfire.info/warning/error("msg", key=val)`.

### Logging Requirements

Add logfire logging for: API calls, error handlers, auth/permission checks, background tasks, data operations, form submissions. Never silently catch exceptions — always `logfire.error("msg", error=str(e))`. Use `logfire.span()` for multi-step operations. Include context: `user_id`, `discord_id`, `zwid` — ids only, never names, email, birth year, measurements or free text (see Personal Data & GDPR).

Levels: `error` (failures/exceptions), `warning` (rate limits/fallbacks), `info` (operations/actions), `debug` (counts/diagnostics).

**Two ways a URL leaks past the kwargs.** `instrument_httpx()` records every request URL as a span attribute, so fetching a page whose URL is itself personal data (a rider's YouTube channel) leaks it through tracing even when no kwarg carries it — wrap that one request in `logfire.suppress_instrumentation()`. And httpx quotes the failing URL in its own exception message, so the usual `error=str(e)` on an `httpx.HTTPError` re-leaks it — log `status_code` (off `HTTPStatusError.response`) or `type(e).__name__` instead. `apps/accounts/utils.py:extract_youtube_channel_id` does both, logging `apps/accounts/utils.py:youtube_url_form` (`handle` / `channel` / `c` / `user` / `non_youtube` / `other`) so a failure is still triageable; guarded by `apps/accounts/test_youtube_logging_privacy.py`.

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

There is no Django signal — code paths that mutate `RaceReadyRecord` must call `refresh_race_ready()` themselves.
It returns `(is_race_ready, is_extra_verified)` — unpack it, as `apps/team/services.py:delete_verification_records` does.

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
| 40-50  | D-E      | weight_full **or** weight_light, height |
| (none) | -        | weight_light, height (default) |

When a category lists both weight types, either satisfies; every other listed type is required (`User.calculate_race_ready()`).
Constance serves a stored row over a changed default, so read `config.CATEGORY_REQUIREMENTS`, not settings.py, for the effective rule.

`get_user_required_verification_types(user)` (`apps/team/services.py`) returns the required types — used by `User.calculate_race_ready()`
and the `/user/verification/` summary. `get_user_verification_types(user)` is the wider list a rider may submit (required types,
`power` always, and `weight_light` once a `weight_full` is verified) — it fills the submission form.

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
   - `EXPIRE_WARNING_DAYS` (JSON list, default `[15, 7, 3, 1]`) — `warn_expiring_verifications` (`apps/team/tasks.py`; registered in `task_registry.py` with `days=None` so it reads this setting) DMs the most urgent threshold a record has crossed but not yet been warned about: `last_warned_threshold` makes a missed run catch up, `last_warned_at` caps it at one DM per record per day. Only each type's covering record counts, and `discord_dm_opt_out` riders are skipped. Also manually triggerable from `/site/config/background_tasks/`.
7. `RaceReadyRecord.days_remaining` property returns days until expiration (or None)

### Race Ready Role Assignment

The platform sets the `RACE_READY_ROLE_ID` role itself through `discord_service`: `sync_race_ready_roles` (`apps/accounts/tasks.py`, every `SCHEDULER_SYNC_RACE_READY_ROLES_HOURS`, default 6h) adds or removes it for every Discord-linked user from the cached `is_race_ready`, judged against the stored `User.discord_roles`; `notify_race_ready_change` (`apps/team/tasks.py`) does it right after a review or a rider's self-delete. The bot also adds or removes it from `/my_profile` and `/sync_user_roles` responses. `RACE_READY_ROLE_ID=0` disables all of these. Roster at `/team/roster/` shows race ready status.

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
6. Approval does **not** gate login — any member of the `GUILD_ID` server with a verified Discord email who isn't blocked can sign in. Approval locks the registration against edits, posts a status notification, and lets the rider import it onto their profile (`/user/profile/import/<uuid>/`); page access still comes from the `team_member` Discord role

### MembershipApplication Model (`apps/team/models.py`)

**Status Choices:**

| Status | Description |
|--------|-------------|
| `pending` | Awaiting review |
| `in_progress` | Admin is reviewing |
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

Registration updates posted to `REGISTRATION_UPDATES_CHANNEL_ID` (set to `0` to disable). Events: applicant update (only if a field changed), status change, admin notes (only if the status didn't also change). New registrations are deliberately **not** announced. Background task `notify_application_update()` in `apps/team/tasks.py` — enqueued async, skips gracefully if not configured.
