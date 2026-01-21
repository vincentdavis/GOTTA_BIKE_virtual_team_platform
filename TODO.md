#TODO list

**Site**

**Data Connections**

- [X] pop up after creating the spreadsheet "Your spreadsheet is created, click Sync to get started."
- [X] If a user deletes the spreadsheet and then click sync, we get a server error, we need to handle this case.
- [X] Users with access to data connections can see all data connections. Not just the ones they created.
- [X] Spreadsheet must be in the GOOGLE_DRIVE_FOLDER_ID Verify this is using an exciting sheet.
- [] verify spreadsheet in owned by organization

**verifications**

- [] Add a provisional status to verification records

**Permissions**

**Tasks**

**Discord**

- [X] post message the use change when a rider leaves the zp team or zr team. This can be a second task triggered by
  sync
  task.

**USER REGISTRATION**

- [] send a message to WELCOM_TEAM_CAHNNEL_ID when a MembershipApplication is created or modified. "{discord nickname}
  Registration record was just updated. Status, "
- [] WelcomE team id 1463214498335817958
- [] Send a message to user from app
- [] Click name to open discord user profile

**Performance Reviews**

- [] add min and max ftp and 120 day min/max ftp
- [] Number of ZP events and ZP races in the last 120 days
- [] wkg min/max 120 days min/max wkg.
- [] plot the data
- [X] /team/performance-review/ is very sloe, maybe because of history

**Membership**

- [] Show is a user has "member role" or "guest role"
- [X] List trainer and heartrate value on /team/membership-review/ Race Profile

**User Profile**

- [X] create a public user profile page
- [] show zp and zr stats on profile page

---

# Logging Audit - Areas Needing Improvement

**Overall Status**: 26% coverage (11 of 42 core files have logging)

## CRITICAL - Fix First

### Syntax Errors in Exception Handlers

- [X] `apps/zwiftracing/tasks.py:29` - Fix `except InvalidOperation, ValueError:` →
  `except (InvalidOperation, ValueError):`
- [X] `apps/zwiftracing/tasks.py:44` - Fix `except ValueError, TypeError:` → `except (ValueError, TypeError):`

### Views Without Logging (High Traffic, Business Critical)

- [X] `apps/team/views.py` (1,138 lines) - LOGGING ADDED
    - [X] Add logging to `verification_record_detail_view()` - record approval/rejection
    - [X] Add logging to `membership_application_admin_view()` - admin review actions
    - [X] Add logging to `membership_application_public_view()` - public form submissions
    - [X] Add logging to `delete_expired_media_view()` - bulk file deletions
    - [X] Add logging to `delete_rejected_media_view()` - bulk file deletions
    - [X] Add logging to `application_verify_zwift()` - Zwift verification failures
    - [X] Add logging to team link CRUD operations
    - [X] Add logging to permission denial cases

- [X] `apps/dbot_api/api.py` (1,026 lines) - LOGGING ADDED
    - [X] Add logging to authentication failures
    - [X] Add logging to all sync operations (roles, user roles, guild members)
    - [X] Add logging for task queue triggers
    - [X] Add logging for roster filter creation
    - [X] Add logging for membership application creation via bot

## HIGH Priority

### Models - Permission & Status Checks

- [] `apps/accounts/models.py` (717 lines) - NO LOGGING
    - [] Add logging to `User.has_permission()` - log permission denials with reason
    - [] Add logging to `User.is_race_ready` property - log status changes
    - [] Add logging to `User.is_profile_complete` property
    - [] Add logging to role management methods

- [] `apps/team/models.py` (945 lines) - NO LOGGING
    - [] Add logging to `RaceReadyRecord.is_expired` - expiration checks
    - [] Add logging to `RaceReadyRecord.delete_media_file()` - file deletion operations
    - [] Add logging to `MembershipApplication` status transitions

### Services - Data Operations

- [X] `apps/team/services.py` (780 lines) - LOGGING ADDED
    - [X] Add logfire import and error logging
    - [X] Add logging to `get_unified_team_roster()` - complex data merging
    - [X] Add logging to `get_membership_review_data()` - data compilation
    - [X] Add logging to `get_performance_review_data()` - calculations
    - [X] Add logging for JSON parsing errors in category requirements (lines 61-66, now logged)

### API Clients

- [X] `apps/zwiftracing/zr_client.py` (147 lines) - LOGGING ADDED
    - [X] Add logfire import
    - [X] Add logging to API calls (get_club, get_rider, etc.)
    - [X] Add logging for rate limit (429) responses with retry_after info

### Decorators

- [] `apps/accounts/decorators.py` (149 lines) - NO LOGGING
    - [] Add logging to `discord_permission_required` - log permission check failures with user context

## MEDIUM Priority

### Forms - Validation Logging

- [] `apps/accounts/forms.py` (370 lines) - NO LOGGING
    - [] Add logging for profile validation errors
    - [] Add logging for password validation failures

- [] `apps/team/forms.py` (657 lines) - NO LOGGING
    - [] Add logging for membership application validation
    - [] Add logging for agreement validation failures
    - [] Add logging for RaceReadyRecord form processing

- [] `apps/data_connection/forms.py` (230 lines) - NO LOGGING
    - [] Add logging for field selection validation

### Admin - Audit Trail

- [] `apps/accounts/admin.py` (319 lines) - NO LOGGING
    - [] Add logging for user permission assignments
    - [] Add logging for bulk operations
    - [] Add logging for custom admin actions

- [] `apps/team/admin.py` (255 lines) - NO LOGGING
    - [] Add logging for bulk verification record operations

- [] `apps/zwiftpower/admin.py` (252 lines) - NO LOGGING
    - [] Add logging for bulk ZP rider updates

- [] `apps/zwiftracing/admin.py` (164 lines) - NO LOGGING

- [] `apps/data_connection/admin.py` - NO LOGGING

## LOW Priority - Silent Error Handlers

These try/except blocks swallow exceptions without logging:

- [] `apps/accounts/tasks.py:33-38` - User lookup for Discord mention (currently silent pass)
- [X] `apps/team/services.py:61-66` - JSON config parsing (now logged with error context)
- [] `apps/team/views.py:79-83` - ValueError in int conversion (silent pass)
- [] `apps/team/views.py:717-722` - ValueError in category filter parsing (silent pass)

## Files with GOOD Logging (Reference Examples)

These files have good logging patterns to follow:

- `apps/zwiftpower/tasks.py` - Comprehensive task logging with spans
- `apps/zwiftpower/zp_client.py` - API client with session and error logging
- `apps/accounts/discord_service.py` - Discord API with status logging
- `apps/accounts/adapters.py` - OAuth flow logging

## Logging Level Guidelines

| Level               | Use For                                                                         |
|---------------------|---------------------------------------------------------------------------------|
| `logfire.error()`   | Permission denials, verification rejections, API failures, file deletion errors |
| `logfire.warning()` | Profile incompleteness, config parsing fallbacks, rate limiting                 |
| `logfire.info()`    | Form submissions, profile updates, roster filtering, admin actions              |
| `logfire.debug()`   | Property calculations, data merging details, filter evaluations                 |

## Implementation Notes

- Use `with logfire.span("operation_name"):` for operations that involve multiple steps
- Include context: `user_id`, `discord_id`, `operation_type`, `affected_records`
- Log at entry AND exit of critical functions (especially for debugging slow operations)
- For try/except blocks, always log the exception with `logfire.error("message", error=str(e))`
