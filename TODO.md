#TODO list

**Site**

**Data Connections**

- [] verify spreadsheet in owned by how the organization

**verifications**

- [] Add a provisional status to verification records

**Permissions**

**Tasks**

**Discord**

**USER REGISTRATION**

- [] Send a message to user from app
- [] Click name to open discord user profile

**Performance Reviews**

- [] add min and max ftp and 120 day min/max ftp
- [] Number of ZP events and ZP races in the last 120 days
- [] wkg min/max 120 days min/max wkg.
- [] plot the data
- [X] /team/performance-review/ is very sloe, maybe because of history

**Membership**

- [] Show if a user has "member role" or "guest role"
- [X] List trainer and heartrate value on /team/membership-review/ Race Profile

**User Profile**

- [X] create a public user profile page
- [] show zp and zr stats on profile page

---

# Logging Audit - Areas Needing Improvement

**Overall Status**: 40% coverage (17 of 42 core files have logging)

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

- [X] `apps/accounts/models.py` (717 lines) - LOGGING ADDED
    - [X] Add logging to `User.has_permission()` - log permission denials with reason
    - [X] Add logging to `User.is_race_ready` property - log status changes
    - [X] Add logging to `User.is_profile_complete` property
    - [X] Add logging to role management methods (`add_role`, `remove_role`)

- [X] `apps/team/models.py` (945 lines) - LOGGING ADDED
    - [X] Add logging to `RaceReadyRecord.is_expired` - expiration checks
    - [X] Add logging to `RaceReadyRecord.delete_media_file()` - file deletion operations
    - [X] Add logging to `MembershipApplication` status transitions (via save method)

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

- [X] `apps/accounts/decorators.py` (149 lines) - LOGGING ADDED
    - [X] Add logging to `discord_permission_required` - log permission check failures with user context

## MEDIUM Priority

### Forms - Validation Logging

- [X] `apps/accounts/forms.py` - LOGGING ADDED
    - [X] Add logging for profile validation errors (birth year validation)

- [X] `apps/team/forms.py` - LOGGING ADDED
    - [X] Add logging for membership application validation (birth year, agreements)
    - [X] Add logging for agreement validation failures
    - [X] Add logging for RaceReadyRecord form processing (media file, required fields)

- [X] `apps/data_connection/forms.py` - LOGGING ADDED
    - [X] Add logging for spreadsheet URL validation

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
