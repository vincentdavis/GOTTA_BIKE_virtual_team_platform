# Discord Bot

The Discord bot provides slash commands for users to interact with the team platform.

## Setup

### Requirements

1. Create a bot at [Discord Developer Portal](https://discord.com/developers/applications)
2. Enable these Privileged Gateway Intents:
   - **Server Members Intent** - Required for guild member sync
3. Invite bot to your server with required permissions:
   - Read Messages/View Channels
   - Send Messages
   - Manage Roles (for race ready role assignment)

### Environment Variables

```bash
DISCORD_BOT_TOKEN=your_bot_token
DISCORD_GUILD_ID=your_guild_id  # For instant command registration
API_BASE_URL=https://your-domain.com
API_KEY=your_dbot_auth_key  # Must match DBOT_AUTH_KEY in Django
```

### Instant Command Registration

By default, Discord slash commands take up to 1 hour to register globally. For instant registration during development:

```python
# src/bot.py
guild_id = os.getenv("DISCORD_GUILD_ID")
debug_guilds = [int(guild_id)] if guild_id else None
bot = commands.Bot(intents=intents, debug_guilds=debug_guilds)
```

## Cogs

The bot is organized into cogs (modules):

| Cog | File | Description |
|-----|------|-------------|
| About | `about.py` | Informational commands |
| Diagnostics | `diagnostics.py` | Debug commands (debug mode only) |
| MemberSync | `member_sync.py` | Sync Discord members to Django |
| RoleSync | `role_sync.py` | Sync Discord roles to Django |
| TeamLinks | `team_links.py` | Team link magic URLs |
| ZwiftPower | `zwiftpower.py` | ZwiftPower and profile commands |

## Slash Commands

### Everyone

| Command | Description |
|---------|-------------|
| `/help` | Get philosophical wisdom about Zwift racing |
| `/my_profile` | View your combined ZwiftPower and Zwift Racing profile |
| `/teammate_profile` | View a teammate's profile (with autocomplete search) |
| `/team_links` | Get a magic link to the team links page |
| `/sync_my_roles` | Sync your Discord roles to the team database |

### Debug Mode Only

| Command | Description |
|---------|-------------|
| `/diag` | Show diagnostic information (Discord ID, roles, etc.) |

### Admin Only

| Command | Description |
|---------|-------------|
| `/sync_members` | Sync all guild members to Django database |
| `/sync_roles` | Manually sync all guild roles to the database |
| `/update_zp_team` | Trigger ZwiftPower team roster update |
| `/update_zp_results` | Trigger ZwiftPower team results update |

## Background Tasks

The RoleSync cog runs automatic background tasks:

| Task | Trigger |
|------|---------|
| Sync all roles | On bot ready |
| Periodic sync | Every hour |
| Role created | When a role is created |
| Role deleted | When a role is deleted |
| Role updated | When a role is updated |
| Member update | When a member's roles change |

## API Integration

The bot communicates with Django via the Discord Bot API:

| Endpoint | Description |
|----------|-------------|
| `GET /api/dbot/my_profile` | Get requesting user's profile |
| `GET /api/dbot/teammate_profile/{zwid}` | Get teammate's profile |
| `GET /api/dbot/zwiftpower_profile/{zwid}` | Get ZwiftPower data |
| `POST /api/dbot/sync_guild_roles` | Sync all Discord roles |
| `POST /api/dbot/sync_guild_members` | Sync all guild members |
| `POST /api/dbot/sync_user_roles/{discord_id}` | Sync a user's roles |

### Authentication

All API requests require:
- `X-API-Key` header - Must match `DBOT_AUTH_KEY`
- `X-Guild-Id` header - Must match `GUILD_ID`
- `X-Discord-User-Id` header - The requesting user's Discord ID

## Race Ready Role Assignment

When users run `/my_profile` or `/sync_my_roles`:

1. API returns `is_race_ready` status and `race_ready_role_id`
2. Bot checks if user should have the race ready role
3. Bot adds/removes the role as needed

Configure `RACE_READY_ROLE_ID` in Django admin (set to `0` to disable).

## Troubleshooting

### Commands not visible

1. Wait up to 1 hour for global registration, OR
2. Set `DISCORD_GUILD_ID` for instant registration to that guild

### Bot can't assign roles

1. Ensure bot has `Manage Roles` permission
2. The role to assign must be below the bot's highest role

### API returns 401/403

1. Check `API_KEY` matches `DBOT_AUTH_KEY` in Django
2. Check `DISCORD_GUILD_ID` matches `GUILD_ID` in Django
