# TODO

## P0 - Critical

### Privacy & Security

- [ ] **Retire the legacy Zwift credential flow — escalated to P0.** `apps/zwift/utils.py:fetch_zwift_id`
  sends the rider's Zwift email and password as **URL query parameters** to a third-party endpoint, and logs
  the email. Two things make this P0 rather than part of the P1 zauth cleanup below: (a) one of the two
  callers, `apps/team/views.py:application_verify_zwift`, is reachable from the **public, unauthenticated**
  registration form — so people who are not yet members type a real password into it; (b) `settings.py` calls
  `logfire.instrument_httpx()` with no `scrubbing=` config and `pw` is not a default-scrubbed key, so full
  URLs reach traces. The zauth OAuth replacement already works. Remove both call sites and the helper, then
  do the rest of the cleanup as part of the P1 zauth item. Consider rotating anything exposed in old traces.
- [x] **The delete-account page makes a false promise.** `templates/accounts/profile_delete.html` says "All
  your data will be permanently deleted." It is wrong in both directions. It *omits* things that genuinely
  are destroyed (event signups + custom answers, squad memberships and captaincies, availability responses,
  DS assignments, race registrations, API keys, magic links, TTT plans, ladder matchups, YouTube videos, MFA
  authenticators), and it *overstates* the rest — these survive `user.delete()`: `GuildMember` (SET_NULL,
  keeps the whole Discord identity), `MembershipApplication` (**no User FK at all**, keyed by `discord_id` —
  a complete second copy of name/email/birth year/gender/country/zwid), `Ticket.details` + `.resolution`
  (SET_NULL), `PageVisit` incl. IP address (SET_NULL), `ZPTeamRiders`/`ZPRiderResults`/`ZRRider` (keyed by
  zwid), `ClubActivity`, and the verification media blobs themselves. Rewrite the copy to match reality.
  Worth doing on its own merits even if the rest of the privacy work never happens.
  *(Done — `templates/accounts/profile_delete.html` now lists both what is deleted and what is kept,
  guarded by `apps/accounts/test_delete_account_copy.py`. The underlying behaviour is unchanged; the
  deletion-parity items under Privacy & Data Protection are what actually close the gaps.)*
- [x] **Log account deletion.** `apps/accounts/views.py:profile_delete` is `logout(); user.delete()` with
  zero logfire calls — the most destructive action in the app leaves no trace, while deleting a single
  verification record is fully logged (`apps/team/views.py:1022`). Contradicts the logging rule in CLAUDE.md.
  *(Done — logs user_id / discord_id / zwid, counts of what cascaded, and the paths of the media blobs that
  are now orphaned in storage; name and email are deliberately excluded. A rejected confirmation is logged
  too. Guarded by `apps/accounts/test_delete_account_audit.py`.)*

### Testing (no test coverage exists)

- [x] Set up pytest config (conftest.py, fixtures, pytest settings in pyproject.toml)
- [ ] Fix latent fresh-DB bug in `apps/accounts/migrations/0013_add_is_race_ready_cached_field.py` — the
  data migration imports the live `User` model, so a fresh `migrate` fails with `no such column:
  accounts_user.has_jersey` (added later in 0017). Pytest currently bypasses this via `--no-migrations`.
- [ ] Permission system tests (has_permission, decorators, role checks)
- [x] Race ready verification logic tests (expiration, category requirements, is_race_ready)
- [ ] Membership application workflow tests (status transitions, form validation)
- [ ] Discord bot API endpoint tests (auth, sync, CRUD operations)
- [ ] Background task tests (ZP sync, ZR sync, Strava sync, notifications)
- [ ] User model tests (profile completion, properties, social account reconnection)
- [ ] CMS page tests (access control, publishing, navigation)

### Error Handling

- [ ] Fix silent `except ValueError: pass` in team/views.py (roster date parsing, category filter)
- [ ] Fix silent `except` in accounts/tasks.py (User lookup for Discord mention)
- [ ] Add file upload validation (MIME type, file size limits) for race ready records

## P1 - High Priority

### Events & Squads

- [ ] **Signup notes leak through the CSV export.** `apps/events/views.py:1240` gates the Notes *column* on
  `is_event_admin`, with a comment stating the promise: "Riders are told the notes box is 'for the event
  admins', so keep that promise." But the CSV export writes `signup.notes` unconditionally (`:2010`), and its
  gate `_can_export_event_signups` admits head-captain and coordinator role holders — a different,
  non-overlapping set. A head captain blocked from the column in the UI can read every rider's notes by
  clicking Export. ~15 min fix: gate the column, or widen the UI gate to match, but make them agree.
- [ ] **Confirm the availability-results read gate is intended.** `availability_results_view`
  (`apps/events/views.py:5160`) is gated only by `@team_member_required()`;
  `_can_manage_squad_availability` is computed but gates editing only. Any team member who loads the URL gets
  every responder's name plus the complete per-rider availability payload as inline JSON. A rider's weekly
  availability is effectively a personal calendar. Same shape at `all_scheduled_races_view` (`:2315`), which
  lists every selected rider's name, zwid, category, rating, age and phenotype across all events.

- [ ] Wire `DS_ROLE_ID` into the DS feature alongside the squad role. The `DS_ROLE_ID` Constance setting exists
  (Discord Guild fieldset) but is unused; when set, also assign/remove it (in addition to the squad
  `team_discord_role`) on DS add/remove in `apps/events/ds_service.py` + `remove_expired_ds_roles` sweep.
- [ ] Document Discord bot channel permissions required for "Create Discord Thread" (View Channel + Create Public
  Threads + Send Messages in Threads); investigate startup preflight check

### Discord Sync

- [x] Add `sync_discord_roles` background task (callable from `/site/config/background_tasks/`)
- [ ] Confirm the external cron service is calling `sync_discord_roles` on a schedule (task is registered; scheduling
  lives outside the repo)
- [ ] Decide what to do with `guild_member_sync_status` now that `sync_guild_members` runs on the platform.
  Options: keep both (defensive), remove the status task and its `SCHEDULER_GUILD_MEMBER_SYNC_STATUS_HOURS`
  setting as redundant, or repurpose it to post a Discord alert when `hours_since_last_sync` exceeds a threshold.
- [ ] Auto-cleanup on guild departure. Today, when `apply_guild_member_sync` stamps `date_left` it only files a
  member-left ticket (now with a squad/leadership/signup cleanup checklist — see
  `apps/tickets/services.py:_member_cleanup_lines`). Discord already strips the departed member's roles, but the
  app keeps stale state. Tiered options to automate, conservative first:
  (A) clear the linked `User.discord_roles` cache so app permissions/badges reflect the departure (safe,
  self-healing on rejoin); (B) also drop them from `Squad.captains`/`vice_captains`; (C) full roster removal
  (`SquadMember`, race `selected_users`/`substitutes`, `EventSignup`) — destructive, not restored on rejoin.
  **Guard required**: only run cleanup on a "healthy" sync (plausible member count, not near-zero / partial page)
  so an incomplete Discord fetch can't wrongly strip many active members. Consider gating B/C behind admin
  confirmation on the ticket, or only after `date_left` persists across N consecutive syncs. The 404-on-removal
  case is already handled idempotently in `apps/accounts/discord_service.py:remove_discord_role`.

### Performance Review

- [ ] Add min/max FTP and 120-day min/max FTP
- [ ] Number of ZP events and ZP races in last 120 days
- [ ] wkg min/max and 120-day min/max wkg
- [ ] Plot the data (charts/graphs)

### Verifications

- [ ] **Auto-purge expired verification media (daily).** Today nothing removes uploaded photos/videos on a
  schedule: purging is two manual admin buttons, `apps/team/views.py:delete_expired_media_view` (expired
  *verified* records) and `delete_rejected_media_view` (rejected records older than 30 days). So a member's
  evidence sits in storage indefinitely until someone remembers to click. Extract the loop from
  `delete_expired_media_view` into a task in `apps/team/tasks.py` (it already does the right thing:
  `record.delete_media_file()` then `save(update_fields=["url", "media_file"])` — the save is required or the
  column keeps the deleted file's name), register it in `gotta_bike_platform/task_registry.py` with
  `scheduled=True` and a new `SCHEDULER_PURGE_EXPIRED_MEDIA_HOURS` Constance setting defaulting to **24**, and add
  that key to the `Scheduler` fieldset in `settings.py`. Keep the manual button working (both should call the same
  helper). Consider folding the rejected-media purge into the same daily run. Also fix the stale claim in
  `apps/team/models.py:32` — the docstring says media "is deleted on verification", which is not true and is
  quoted to members on the delete-account page.

- [ ] Group `warn_expiring_verifications` DMs by user (currently one DM per matching record per day; consolidate)
- [ ] **zauth migration cleanup** — the legacy Sauce-mod password verification is now *hidden, not removed*.
  The UI triggers are wrapped in `{% comment %}` blocks marked `TODO(zauth-cleanup)` in
  `templates/accounts/partials/zwift_status.html`, `templates/accounts/partials/profile_form.html`, and
  `templates/team/partials/application_zwift_status.html`. Once zauth has proven out, delete:
  - views `accounts:verify_zwift` / `accounts:unverify_zwift` and `team:application_verify_zwift` /
    `team:application_unverify_zwift`, plus their URL entries
  - templates `accounts/partials/zwift_verify_modal.html`, `team/partials/application_zwift_verify_modal.html`,
    and the `<dialog id="zwift-verify-modal">` blocks that host them
  - forms `ZwiftVerificationForm` / `ApplicationZwiftVerificationForm`
  - `apps/zwift/utils.py:fetch_zwift_id` (sends Zwift credentials as URL query params to a third-party
    endpoint — the reason for the migration) once no caller remains
  Keep `manual_zwift_verify` / `application_manual_zwift_verify`: those are the admin-reviewed fallback for
  members who can't use Zwift OAuth, and are unrelated to the password flow.
- [ ] A membership application's zauth connection is keyed by the application UUID, so it does **not** carry
  over to the User account created at first login (the reconcile ignores non-numeric ids by design). New
  members currently have to connect again from their profile — decide whether to re-key on approval or leave as is.

### Privacy & Data Protection (`/user/profile/mydata`)

Full plan and inventory live outside this repo (this repo is public). Context that drives the ordering:
**retention today is "forever" almost everywhere** — there are 28 tasks in
`gotta_bike_platform/task_registry.py` and exactly one deletes anything (`purge_expired_api_keys`). So there
is no retention policy to describe; building this page means writing one. Mount at
`path("profile/mydata/", ...)` in `apps/accounts/urls.py` (accounts is mounted at `/user/`).

Publish the privacy policy itself as a CMS page and point the existing `PRIVACY_POLICY_URL` Constance
setting at it — that is separate from this per-rider page, and costs no code.

**The page, read-only:**

- [ ] "What we hold about you", grouped by where it came from: your profile / from Discord / from Zwift /
  from ZwiftPower + ZwiftRacing ("we hold this because you appear in club results, whether or not you use
  this site") / your verification records incl. whether the uploaded media still exists / your racing
  (signups, custom answers, availability, squads) / your tickets / technical (API keys, sessions, visits).
  Build the collection step as a reusable `apps/accounts/services.py:collect_user_data(user)` so the
  download below falls out of it nearly free.
- [ ] **"Who has seen my verification evidence"** — best value-per-hour item on this list. `RecordView`
  (`apps/team/models.py:395-427`) *already* records per-viewer open counts and, separately, whether the media
  was actually rendered. It is shown on the record-detail page, which bounces anyone without
  `can_approve_verification` (`apps/team/views.py:930`) — so the subject of the photo is the one person who
  cannot see their own audit trail. It is a query and a table.
- [ ] Retention summary table: what we keep and for how long. Writing this honestly is what forces the
  retention decisions below. Put it on the CMS privacy page rather than hardcoding it in the template.

**Data download:**

- [ ] `GET /user/profile/mydata/download/` returning `collect_user_data()` as JSON. (Supersedes the old
  "Personal data export page (/user/export/)" line under P3.) If a CSV variant is added, reuse `_csv_safe`
  from `apps/events/views.py`.
- [ ] Optionally include the rider's own verification media in the download. Stream it — uploads can be
  50 MB (`apps/team/forms.py:265`).

**Make deletion match the promise** (each independently shippable; consider one
`apps/accounts/services.py:delete_user_data(user, *, reason)` in a transaction, called by both the
self-serve view and an admin action — deletion spread across a view body is how the current gaps happened):

- [ ] Delete verification media files **before** the cascade. Note: `user.delete()` cascades through
  Django's Collector, which never calls `Model.delete()` per instance — so overriding
  `RaceReadyRecord.delete()` will *not* work; the delete path must iterate explicitly. Same bug at
  `apps/team/views.py:1031` (single-record delete orphans the file) while the bulk sweep at `:1287` gets it
  right. Once the row is gone the blob is unfindable except by enumerating the bucket prefix.
- [ ] Call zauth `disconnect()` on account deletion — `apps/zwift/client.py:236` exists and is already used
  at `apps/zwift/views.py:122`, just not wired into the delete path, so the upstream Zwift link survives.
- [ ] Decide and implement: delete the `MembershipApplication` by `discord_id`, or scrub its personal fields
  and keep the row for audit? **Open decision.**
- [ ] Decide and implement: delete `GuildMember`, or clear its identity fields and keep the shell? It is the
  anchor for the member-left ticket flow, so deleting it has knock-on effects.
- [ ] Handle tickets on account deletion. `submitted_by` is SET_NULL, so the free text outlives the author
  *and* (per `visible_tickets`) becomes permanently ticket_admin-only. Harder: `create_member_left_ticket`
  (`apps/tickets/services.py:74-98`) interpolates the member's real name, Discord ID, profile URL and squad
  history directly into `details`, so no FK rule can neutralise it — needs an explicit sweep.
- [ ] Delete `PageVisit` rows on account deletion, or at minimum drop the IP address.
- [ ] Self-service verification-record removal: let a member delete their own record and its media.
  **Open decision:** should deleting a *verified* record revoke race-ready status? `refresh_race_ready()`
  must be called either way.

**Retention in code** (each is a small task + a `SCHEDULER_*_HOURS` setting, following the
`purge_expired_api_keys` pattern):

- [ ] Purge media on pending verification records left unreviewed for N days — neither existing purge button
  can reach a pending record. (The daily expired-media purge is tracked under Verifications above.)
- [ ] `PageVisit` retention — drop rows after 90 days, or drop the IP after 30.
- [ ] Run `clearsessions` on a schedule — it appears **nowhere** in the repo, so expired Django session rows
  never leave the database.
- [ ] One-off sweep for orphaned media blobs in the bucket with no DB row (see the cascade note above).
  `file_overwrite=False` means superseded re-uploads accumulate too.

**Data about people who never signed up** (decide, not necessarily build):

- [ ] Write the "how we might have your data even if you never gave it to us" section — the part most teams
  skip, and the reason it is hard: `ZPTeamRiders` / `ZPRiderResults` / `ZRRider` are keyed by `zwid` with no
  User FK, and `ZPRiderResults` stores per-race weight, height and **heart rate** indefinitely
  (`get_weight_height_history(zwid)` exists specifically to reconstruct a weight-over-time series).
  `ClubActivity` is worse: Strava withholds athlete IDs (`apps/club_strava/models.py:11`), so one person's
  rows **cannot be located or deleted on request** even if asked.
- [ ] Review the Sheets export scope: `apps/data_connection/services.py:383-385` unions zwids from `User`,
  `ZPTeamRiders` **and** `ZRRider`, so exports include riders who never signed up here. Also
  `apps/data_connection/gs_client.py:148` can transfer sheet ownership to an arbitrary external email, after
  which the app has no control over the exported data.

### User Profile

### Membership

- [ ] Show if user has "member role" or "guest role"

### User Registration

- [x] Click name to open Discord user profile

### Data Connections

- [ ] Verify spreadsheet is owned by the organization

### Admin Logging (remaining from audit)

- [ ] accounts/admin.py - Add logging for permission assignments, bulk ops, custom actions
- [ ] team/admin.py - Add logging for bulk verification record operations
- [ ] zwiftpower/admin.py - Add logging for bulk ZP rider updates
- [ ] zwiftracing/admin.py - Add logging
- [ ] data_connection/admin.py - Add logging

### Search & Filtering

- [ ] Add search/filter to Membership Applications page (name, status, date)
- [x] Add filtering to Verification Records (type, status, date range)

### Export

- [ ] CSV export from roster (with current filters applied)
- [ ] CSV export from verification records
- [ ] CSV export from membership applications
- [ ] CSV export from performance review

### Caching

- [ ] Cache get_unified_team_roster() (changes infrequently, expensive query)
- [ ] Cache ZP/ZR API responses (invalidate on manual sync)
- [ ] Cache analytics dashboard queries

## P2 - Medium Priority

### Dashboards

- [ ] Captain dashboard with key metrics (pending apps, race-ready %, recent joins)
- [ ] Admin dashboard with system health (sync status, pending tasks, error rates)

### Data Visualization

- [ ] Race results charts (performance trends, category distribution)
- [ ] Team membership growth over time
- [ ] Race-ready status breakdown (pie/bar chart)

### UX Improvements

- [x] Reusable user tooltip partial (`templates/accounts/_user_tooltip.html`). A single `{% include %}` that wraps any
  user name with a hover tooltip showing avatar, Discord username, race ready status, ZP/ZR category, rating, phenotype,
  and profile links. Accepts optional enriched ZP/ZR context; gracefully omits fields not provided. Replace existing
  per-page tooltip implementations (my_events squad members, event_detail squad members, roster, etc.) with this shared
  partial.
- [ ] Onboarding checklist for new users (profile → verify Zwift → race verification)
- [x] Help/FAQ page with glossary (race-ready, categories, verification types)
- [ ] Full notification center (bell icon + dropdown). Sidebar/avatar badges for pending verifications and pending
  availability already shipped via context processors; this would consolidate them and add new sources.
- [ ] Persistent notification center (beyond auto-dismiss toasts)
- [ ] Mobile-optimized table views (card layout option for small screens — availability grids done, tables remain)
- [ ] Drive the Configuration submenu in `theme/templates/sidebar.html` off `CONSTANCE_CONFIG_FIELDSETS` instead of the
  hardcoded `<li>` list, so adding a new fieldset group automatically appears in the menu (currently a two-step change
  is needed — fieldset entry + manual sidebar `<li>`)

### Accessibility

- [ ] Add ARIA labels to dropdown menus, badges, and status indicators
- [ ] Add skip-to-main-content link
- [ ] Add alt text to all images (avatars, hero, icons)
- [ ] Add focus ring styling for keyboard navigation
- [ ] Form error accessibility (aria-invalid, aria-describedby)

### Security & Infrastructure

- [ ] **MFA/TOTP seeds are stored in plaintext.** allauth's default `MFAAdapter.encrypt()`/`decrypt()` are
  identity functions and no `MFA_ADAPTER` is configured, so the base32 TOTP seed and recovery codes sit
  readable in `mfa_authenticator.data`. Set a custom MFA adapter that actually encrypts.
- [ ] **Discord OAuth tokens are stored even though the setting says not to.** `SOCIALACCOUNT_STORE_TOKENS`
  is unset (allauth default `False`) and allauth's own paths skip the token, but
  `apps/accounts/adapters.py:196-199` saves `sociallogin.token` explicitly, bypassing the flag. Because
  allauth's `_store_token` bails on the same flag the token is never refreshed either — so every user
  created through that path carries a permanently stale cleartext access token. Decide whether the token is
  needed at all; if not, drop those lines and purge the existing `SocialToken` rows.

- [ ] Rate limiting on API endpoints (dbot, cron, analytics)
- [ ] Separate cron API auth key from dbot API key
- [ ] Background task retry logic for transient API failures

### Integrations

- [ ] Integrate Strava activities into Team Feed page
- [ ] Dark mode support (DaisyUI ready, needs config toggle)

## P3 - Nice to Have

- [x] Team calendar/events feature
- [ ] Email notifications (beyond Discord-only)
- [ ] PWA manifest for mobile install
- [ ] CMS page versioning/history
- [ ] SEO meta fields on CMS pages (description, OG tags)
- [ ] Auto-expire stale pending membership applications
- [x] ~~Personal data export page (/user/export/ for GDPR)~~ — superseded by the Privacy & Data Protection section under P1 (`/user/profile/mydata`)
- [ ] API response pagination for large datasets
- [ ] Remove or implement apps/zwift/ placeholder app
