# Guild Member Sync

Syncs Discord guild members with Django to track and compare membership status.

## Overview

The guild sync feature allows you to:
- Track all Discord server members in Django
- Compare Discord members vs. registered users
- Identify members who left the server
- Find users who haven't created accounts yet

## GuildMember Model

Stores Discord guild member data:

| Field | Description |
|-------|-------------|
| `discord_id` | Discord user ID (unique) |
| `username` | Discord username |
| `display_name` | Discord global display name |
| `nickname` | Server-specific nickname |
| `avatar_hash` | Discord avatar hash for CDN URL |
| `roles` | JSON list of Discord role IDs |
| `joined_at` | When the member joined the guild |
| `is_bot` | Whether this member is a bot |
| `date_created` | When this record was created |
| `date_modified` | When this record was last updated |
| `date_left` | When the member left (null if still present) |
| `user` | OneToOne link to User (if they have an account) |

## How to Sync

1. Use the `/sync_members` slash command in Discord (admin only)
2. The bot collects all guild members
3. Bot POSTs member data to `POST /api/dbot/sync_guild_members`
4. Django creates/updates GuildMember records
5. Members not in payload are marked as left (`date_left` set)
6. GuildMembers are linked to User accounts by matching `discord_id`

**Important**: Only affects Discord OAuth users. Regular Django accounts (staff/admin without Discord login) are not affected by the sync.

## Admin Views

### Guild Members List

Access at `/admin/accounts/guildmember/`

Shows all synced guild members with:
- Discord username and display name
- Whether they have a linked User account
- Join date and left date (if applicable)

### Comparison View

Access at `/admin/accounts/guildmember/comparison/`

Shows four categories:

| Category | Description |
|----------|-------------|
| **Guild Only** | Discord members who haven't created a User account |
| **Linked** | Discord members with linked User accounts |
| **Left Guild** | Members who left Discord but have User accounts |
| **Discord Users (No Guild)** | OAuth users without a GuildMember record |

## Discord Bot Setup

The bot requires the **Server Members Intent** (privileged intent):

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Select your bot > Bot > Privileged Gateway Intents
3. Enable **Server Members Intent**

In bot code (`src/bot.py`):
```python
intents = discord.Intents.default()
intents.members = True  # Required for guild member sync
```

## API Endpoint

### POST /api/dbot/sync_guild_members

Syncs all guild members from Discord.

**Headers:**
- `X-API-Key` - Must match `DBOT_AUTH_KEY`
- `X-Guild-Id` - Must match `GUILD_ID`

**Body:**
```json
{
  "members": [
    {
      "id": "123456789",
      "username": "user#1234",
      "display_name": "Display Name",
      "nickname": "Server Nickname",
      "avatar": "avatar_hash",
      "roles": ["role_id_1", "role_id_2"],
      "joined_at": "2024-01-01T00:00:00Z",
      "is_bot": false
    }
  ]
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

## Troubleshooting

### /sync_members command not working

1. Ensure the bot has Server Members Intent enabled
2. Check bot has permission to read guild members
3. Verify `DBOT_AUTH_KEY` and `GUILD_ID` are configured

### GuildMember not linked to User

The `discord_id` on the User must match the GuildMember's `discord_id`. This happens automatically when users log in via Discord OAuth.
