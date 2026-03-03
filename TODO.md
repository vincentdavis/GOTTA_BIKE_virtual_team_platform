# TODO

## P0 - Critical

### Testing (no test coverage exists)

- [ ] Set up pytest config (conftest.py, fixtures, pytest settings in pyproject.toml)
- [ ] Permission system tests (has_permission, decorators, role checks)
- [ ] Race ready verification logic tests (expiration, category requirements, is_race_ready)
- [ ] Membership application workflow tests (status transitions, form validation)
- [ ] Discord bot API endpoint tests (auth, sync, CRUD operations)
- [ ] Cron API endpoint tests
- [ ] Background task tests (ZP sync, ZR sync, Strava sync, notifications)
- [ ] User model tests (profile completion, properties, social account reconnection)
- [ ] CMS page tests (access control, publishing, navigation)

### Error Handling

- [ ] Fix silent `except ValueError: pass` in team/views.py (roster date parsing, category filter)
- [ ] Fix silent `except` in accounts/tasks.py (User lookup for Discord mention)
- [ ] Add file upload validation (MIME type, file size limits) for race ready records

## P1 - High Priority

### Events & Squads

- [x] Role Setup: configurable `EVENT_ROLE_PREFIXES` constance setting with form validation
- [x] Role Setup: read-only Role Setup section on event edit page (editable only on dedicated page)
- [x] Squad form: Discord role requires event prefix, only shows roles matching the prefix
- [x] Manage Roles: Head Captain Role holders can manage roles (in addition to `PERM_ASSIGN_ROLES`)
- [x] Role Setup page: accessible by `assign_roles`, head captain role, or event admins (read-only)
- [x] Availability builder: mobile-responsive grid (smaller cells, scroll arrows, stacked settings, short day labels)
- [x] Availability respond: mobile-responsive grid (smaller cells, scroll arrows, short day labels)
- [ ] Availability: backend processing of responses (aggregate results, export)

### Discord Sync

- [x] Add `sync_discord_roles` background task (callable from `/site/config/background_tasks/`)
- [ ] Auto-sync Discord roles on a schedule (like guild members every 6 hours)

### Performance Review

- [ ] Add min/max FTP and 120-day min/max FTP
- [ ] Number of ZP events and ZP races in last 120 days
- [ ] wkg min/max and 120-day min/max wkg
- [ ] Plot the data (charts/graphs)

### Verifications

- [ ] Add "Extra Verified" tier (`is_extra_verified` — weight_full + height + power all verified, regardless of category)
- [ ] Add provisional status to verification records
- [ ] Backfill historical record_date values (some default to 2026-01-01)

### User Profile

- [x] Show ZP and ZR stats on profile page

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

- [ ] Reusable user tooltip partial (`templates/accounts/_user_tooltip.html`). A single `{% include %}` that wraps any user name with a hover tooltip showing avatar, Discord username, race ready status, ZP/ZR category, rating, phenotype, and profile links. Accepts optional enriched ZP/ZR context; gracefully omits fields not provided. Replace existing per-page tooltip implementations (my_events squad members, event_detail squad members, roster, etc.) with this shared partial.
- [ ] Onboarding checklist for new users (profile → verify Zwift → race verification)
- [x] Help/FAQ page with glossary (race-ready, categories, verification types)
- [ ] Persistent notification center (beyond auto-dismiss toasts)
- [ ] Mobile-optimized table views (card layout option for small screens — availability grids done, tables remain)

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
