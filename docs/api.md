# API Reference

The platform provides two REST APIs built with Django Ninja.

## Discord Bot API

Base URL: `/api/dbot/`

### Authentication

All endpoints require these headers:

| Header | Description |
|--------|-------------|
| `X-API-Key` | Must match `DBOT_AUTH_KEY` constance setting |
| `X-Guild-Id` | Must match `GUILD_ID` constance setting |
| `X-Discord-User-Id` | The requesting user's Discord ID |

### Endpoints

#### GET /api/dbot/my_profile

Get the combined profile for the requesting Discord user.

**Response:**
```json
{
  "user": {
    "discord_id": "123456789",
    "discord_username": "user#1234",
    "first_name": "John",
    "last_name": "Doe",
    "zwid": 12345,
    "zwid_verified": true
  },
  "zwiftpower": {
    "name": "John Doe",
    "team": "The Coalition",
    "ftp": 280,
    "weight": 75.0
  },
  "zwiftracing": {
    "rating": 850,
    "division": 2
  },
  "is_race_ready": true,
  "race_ready_role_id": "1234567890123456789"
}
```

#### GET /api/dbot/teammate_profile/{zwid}

Get the combined profile for any teammate by Zwift ID.

**Parameters:**
- `zwid` (path) - Zwift user ID

**Response:** Same as `/my_profile` (but includes `account` object instead of `discord_username`)

#### GET /api/dbot/search_teammates

Search for teammates by name (for Discord autocomplete).

**Parameters:**
- `q` (query) - Search query (minimum 2 characters)

**Response:**
```json
{
  "results": [
    {
      "zwid": 12345,
      "name": "John Doe",
      "flag": "us"
    }
  ]
}
```

#### GET /api/dbot/team_links

Generate a magic link to the team links page for the requesting Discord user.

**Response:**
```json
{
  "magic_link_url": "https://domain.com/m/abc123/",
  "expires_in_seconds": 300,
  "redirect_to": "/team/links/"
}
```

#### GET /api/dbot/zwiftpower_profile/{zwid}

Get ZwiftPower data for a rider.

**Parameters:**
- `zwid` (path) - Zwift user ID

**Response:**
```json
{
  "name": "John Doe",
  "team": "The Coalition",
  "ftp": 280,
  "weight": 75.0,
  "category": "B",
  "race_count": 42
}
```

#### POST /api/dbot/sync_guild_roles

Sync all Discord roles to Django.

**Body:**
```json
{
  "roles": [
    {
      "id": "123456789",
      "name": "Team Member",
      "color": 16711680,
      "position": 5
    }
  ]
}
```

**Response:**
```json
{
  "synced": 10
}
```

#### POST /api/dbot/sync_guild_members

Sync all guild members to Django. See [Guild Sync](guild-sync.md) for details.

**Body:**
```json
{
  "members": [...]
}
```

**Response:**
```json
{
  "created": 5,
  "updated": 10,
  "marked_left": 2
}
```

#### POST /api/dbot/sync_user_roles/{discord_id}

Sync a single user's Discord roles.

**Parameters:**
- `discord_id` (path) - Discord user ID

**Body:**
```json
{
  "roles": {
    "123456789": "Team Member",
    "987654321": "Captain"
  }
}
```

**Response:**
```json
{
  "updated": true,
  "is_race_ready": true,
  "race_ready_role_id": "1234567890123456789"
}
```

#### POST /api/dbot/roster_filter

Create a filtered roster link from Discord channel members.

**Body:**
```json
{
  "discord_ids": ["123456789", "987654321"],
  "channel_name": "race-team-a"
}
```

**Response:**
```json
{
  "filter_id": "uuid",
  "url": "https://domain.com/team/roster/f/uuid/",
  "expires_in_seconds": 300,
  "member_count": 2,
  "channel_name": "race-team-a"
}
```

#### POST /api/dbot/membership_application

Create a new membership application from Discord modal.

**Body:**
```json
{
  "discord_id": "123456789",
  "discord_username": "user#1234",
  "server_nickname": "Nickname",
  "avatar_url": "https://cdn.discordapp.com/...",
  "modal_form_data": {"interest": "Racing"}
}
```

**Response:**
```json
{
  "id": "uuid",
  "discord_id": "123456789",
  "discord_username": "user#1234",
  "status": "pending",
  "application_url": "https://domain.com/team/apply/uuid/",
  "is_complete": false,
  "date_created": "2024-01-15T10:30:00+00:00",
  "already_exists": false
}
```

#### GET /api/dbot/membership_application/{discord_id}

Get membership application by Discord ID.

**Parameters:**
- `discord_id` (path) - Discord user ID

**Response:**
```json
{
  "id": "uuid",
  "discord_id": "123456789",
  "discord_username": "user#1234",
  "status": "pending",
  "status_display": "Pending Review",
  "application_url": "https://domain.com/team/apply/uuid/",
  "is_complete": true,
  "is_editable": true
}
```

#### POST /api/dbot/update_zp_team

Trigger the ZwiftPower team riders update task.

**Response:**
```json
{
  "status": "queued",
  "message": "ZwiftPower team update task has been queued."
}
```

#### POST /api/dbot/update_zp_results

Trigger the ZwiftPower team results update task.

**Response:**
```json
{
  "status": "queued",
  "message": "ZwiftPower team results update task has been queued."
}
```

---

## Cron API

Base URL: `/api/cron/`

REST API for triggering scheduled tasks via external cron service.

### Authentication

| Header | Description |
|--------|-------------|
| `X-Cron-Key` | Must match `DBOT_AUTH_KEY` constance setting |

### Endpoints

#### GET /api/cron/tasks

List all available tasks.

**Response:**
```json
{
  "tasks": [
    {
      "name": "update_team_riders",
      "description": "Fetch team riders from ZwiftPower"
    },
    {
      "name": "update_team_results",
      "description": "Fetch team results from ZwiftPower"
    },
    {
      "name": "sync_zr_riders",
      "description": "Sync riders from Zwift Racing API"
    }
  ]
}
```

#### POST /api/cron/task/{task_name}

Trigger a task by name.

**Parameters:**
- `task_name` (path) - Name of the task to run

**Response:**
```json
{
  "status": "enqueued",
  "task": "update_team_riders"
}
```

### Available Tasks

| Task | Description |
|------|-------------|
| `update_team_riders` | Fetch team riders from ZwiftPower |
| `update_team_results` | Fetch team results from ZwiftPower |
| `sync_zr_riders` | Sync riders from Zwift Racing API |

### Example Cron Call

```bash
curl -X POST \
  -H "X-Cron-Key: your-key" \
  https://your-domain.com/api/cron/task/update_team_riders
```

### Adding New Tasks

Update `TASK_REGISTRY` in `apps/dbot_api/cron_api.py`:

```python
from apps.your_app.tasks import your_task

TASK_REGISTRY: dict = {
    "your_task_name": {
        "task": your_task,
        "description": "What the task does",
    },
}
```

---

## URL Routes

| Route | Description |
|-------|-------------|
| `/` | Home page |
| `/about/` | About page |
| `/admin/` | Django admin |
| `/accounts/` | allauth (login, logout, MFA) |
| `/user/` | User profile and settings |
| `/team/` | Team management |
| `/team/roster/` | Team roster |
| `/team/roster/f/{uuid}/` | Filtered roster view |
| `/team/links/` | Team links |
| `/team/verification/` | Verification records (approvers only) |
| `/team/applications/` | Membership applications (admins only) |
| `/team/apply/{uuid}/` | Public membership application form |
| `/team/performance-review/` | Performance review |
| `/team/membership-review/` | Membership review (admins only) |
| `/team/team-feed/` | Team social media feed |
| `/page/<slug>/` | CMS pages |
| `/data-connections/` | Google Sheets exports |
| `/site/config/` | Site configuration (admin) |
| `/api/dbot/` | Discord bot API |
| `/api/cron/` | Cron task API |
| `/m/` | Magic links (legacy) |
