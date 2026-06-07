# TODO

## P0 - Critical

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

- [ ] Availability: CSV export of responses
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

- [ ] Group `warn_expiring_verifications` DMs by user (currently one DM per matching record per day; consolidate)

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
- [ ] Personal data export page (/user/export/ for GDPR)
- [ ] API response pagination for large datasets
- [ ] Remove or implement apps/zwift/ placeholder app
