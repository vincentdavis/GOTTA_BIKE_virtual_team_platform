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

Departures reach the platform in two ways:

- **The bot's leave report** — most departures. The bot tells the platform the moment a member leaves, and the departure is recorded at once (see [The leave report](#the-leave-report))
- **The scheduled task** — the backstop. It reads the whole member list and catches any departure the bot missed (the bot was down, or the event was lost), so the longest a departed rider keeps access is `SCHEDULER_SYNC_GUILD_MEMBERS_HOURS`

There are two sync drivers. Both upsert through `apps/accounts/services.py:apply_guild_member_sync`, but only one of them decides who has left.

### The scheduled task (primary)

1. `sync_guild_members` (`apps/accounts/tasks.py`) runs every `SCHEDULER_SYNC_GUILD_MEMBERS_HOURS` (default 6h), or on demand from **Run Now** at `/site/config/background_tasks/`
2. It notes the time (`observed_at`), then reads the whole member list from Discord's REST API, page by page. A page that cannot be continued fails the run rather than hand over a short list
3. Django creates/updates GuildMember records and links them to User accounts by matching `discord_id`
4. Active members missing from the list are marked as left (`date_left` set) and each gets a low-priority Membership ticket. **A departure signs the rider out of the site on their next request and stops their API keys.** A row changed at or after `observed_at` is skipped (`departures_deferred`): that rider rejoined and signed in while the list was being read, and the next run judges them
5. Every Discord-linked site account that no sync has ever listed gets a departed row too, with no ticket, and is signed out. Accounts created or signed in at or after `observed_at` are skipped, because their login's live guild check is newer than the list. These rows are labelled **Never seen in the server** (see below)

### The bot push (fallback)

1. The `/sync_members` slash command in Discord (admin only), or the bot's periodic loop, collects the members in the bot's cache
2. The bot POSTs them to `POST /api/dbot/sync_guild_members`, with `observed_at`: when it read the list
3. Django creates/updates those GuildMember records, and clears `date_left` for any listed member who had been marked as left before `observed_at`

The push **never** marks anybody as left and never records unseen accounts. The bot's gateway cache can be missing whole member chunks after a restart, and a short list would sign real members out. The response says so with `departures_evaluated: false` (`left` is always 0).

### The leave report

1. The bot sees a member leave the server (its member-remove event)
2. It POSTs `POST /api/dbot/member_left/{discord_id}` straight away
3. Django stamps `date_left` on that member's row and files the usual low-priority Membership ticket. **The rider is signed out on their next request and their API keys stop working**

If the member has no row yet (they joined and left between syncs), one is created from what the bot sends and marked as left. It is a real departure, not a **Never seen in the server** row: its `date_left` is always later than its `date_created`. The new row is linked to a site account only when exactly one account has that Discord ID and that account has no row of its own yet. It gets a ticket only when some site account has that Discord ID: for anyone else there is nothing in the app to follow up, and people who join and leave again would fill the queue.

A report for a member already marked as left answers `already_departed` and files no ticket, but moves `date_left` up to now. The member may have come back and left again, and a sync that read its list while they were back would otherwise clear the departure (see [A list older than the departure](#a-list-older-than-the-departure)). A **Never seen in the server** row reported this way becomes an ordinary departure. So a repeated report is safe; it only moves the time. One member per report, so the [safety limits](#safety-limits) do not apply.

The ticket names the linked site account. If that account has since moved to a different Discord ID, the ticket says so and leaves out the cleanup checklist: the account has not left.

### A list older than the departure

A departure recorded by the leave report can be newer than a member list that still includes the rider. So both syncs clear `date_left` only when their list was read **after** the stamp (`date_left` earlier than `observed_at`). Otherwise the stamp stays, the row is counted in `rejoin_deferred`, and the next list decides. The scheduled task sets `observed_at` itself, before the fetch. The bot push sends it. If the push leaves it out or sends something unreadable, the platform uses the time it received the push. A time in the future is treated as now.

A Discord login that passes the live guild check always clears the stamp: Discord has just confirmed the rider is back.

### Safety limits

Both sign-out steps of the scheduled task are held back when the list does not look complete:

| Step | Held back when | Result keys |
|------|----------------|-------------|
| Departures | the list is empty, or more than 10 **and** more than 5% of the active members would be marked as left at once | `departures_refused`, `departures_skipped` |
| Never-seen accounts | the list is empty, or more than 10 **and** more than 5% of the Discord-linked site accounts would be recorded at once. Only runs once departures have passed | `unseen_refused`, `unseen_skipped` |

`*_refused` is `empty_member_list` or `mass_departure`. A held-back run still saves the members it received, and then:

- it opens one high-priority **Membership** ticket, "Guild member sync is holding back sign-outs", with the counts and what to do. While that ticket is open (new or in progress), later refused runs update it instead of filing another; once it is closed, the next refusal opens a new one
- the task run is recorded as **Failed** on the Run Now page (`GuildSyncRefusedError`)
- every later run is refused the same way, so **nobody who leaves the server is signed out until an admin acts**

**If the numbers are real** (for example, a prune in Discord, or the first run after a fresh deploy with many never-seen accounts), open `/site/config/background_tasks/`, tick **Accept a mass departure** on `sync_guild_members` and run it. That lifts both limits for that run. It never applies to an empty list: an empty list means the bot token, `GUILD_ID` or the Server Members intent needs checking.

**If they are not**, run `sync_guild_members` again without ticking the box. Close the ticket once a run finishes without being refused.

### Never seen in the server

A row the sync wrote for an account it has never listed is not a departure: nobody saw that account in the server. The row is created already departed, with `date_left` equal to `date_created`, and that is how the views tell it apart (`never_seen_in_guild()` in `apps/accounts/services.py`). A real departure is always stamped later than its row was created. If a never-seen account later appears in a sync, or signs in with Discord while in the server, the stamp is cleared, and a later departure is a real one.

**Important**: Only affects Discord OAuth users. Regular Django accounts (staff/admin without Discord login) are not affected by the sync.

## Admin Views

### Guild Members List

Access at `/admin/accounts/guildmember/`

Shows all synced guild members with:
- Discord username and display name
- Whether they have a linked User account
- Join date and left date (if applicable)

### Discord Review

Access at `/team/discord-review/` (`membership_admin`). The Status filter offers Active, Left and **Never seen in the server**; "Left" leaves out the never-seen rows, and the table and CSV export label them.

### Comparison View

Access at `/admin/accounts/guildmember/comparison/`

Shows five categories:

| Category | Description |
|----------|-------------|
| **Guild Only** | Active Discord members who haven't created a User account |
| **Linked** | Active Discord members with linked User accounts |
| **Left Guild** | Departed members whose Discord ID belongs to a User account |
| **Never Seen in the Server** | User accounts whose Discord ID no sync has ever listed (recorded as departed) |
| **Discord Users (No Guild)** | OAuth users without any GuildMember record yet (until the next scheduled sync records them) |

The two departed categories match rows to accounts by `discord_id`, as the sign-out rule does, not by the row's user link. So a row left unlinked (several accounts share the Discord ID, or the account is already linked to an older row) is still listed, with the account name marked "not linked".

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

## API Endpoints

### POST /api/dbot/member_left/{discord_id}

Records that one member has left, as the bot saw it happen.

**Headers:** as below. `X-Discord-User-Id` is the bot's own user ID, since no person triggered the call.

**Path:** `discord_id` — the departed member's Discord ID, 1–20 ASCII digits. Anything else gets **400**.

**Body:** JSON, every field optional; `{}` is valid. The fields are only used to create a row when the platform has none for that ID. `null` counts as blank.
```json
{
  "username": "leaver",
  "display_name": "The Leaver",
  "avatar_hash": "a1b2c3",
  "is_bot": false
}
```

**Response (200):**
```json
{"status": "departed", "created": false, "ticket_created": true}
```

| Field | Meaning |
|-------|---------|
| `status` | `departed` (stamped now) or `already_departed` (it already was; the stamp is moved up to now, no ticket) |
| `created` | A new row was created for a member the platform had no row for |
| `ticket_created` | A member-left ticket was filed (false if one is already open, filing failed, the member was already departed, or the row is new and no site account has that ID) |

**Errors:** 401 for a wrong key or guild, or a missing header. 400 for an ID that is not all digits, or a missing or non-JSON body (send at least `{}`). 422 for a field of the wrong type.

### POST /api/dbot/sync_guild_members

Refreshes the guild members the bot lists. It does not mark anybody as left.

**Headers:**
- `X-API-Key` - Must match `DBOT_AUTH_KEY`
- `X-Guild-Id` - Must match `GUILD_ID`
- `X-Discord-User-Id` - The Discord ID of whoever ran `/sync_members`, or the bot's own ID for its periodic sync

**Body:**
```json
{
  "observed_at": "2026-09-16T12:00:00Z",
  "members": [
    {
      "discord_id": "123456789",
      "username": "user#1234",
      "display_name": "Display Name",
      "nickname": "Server Nickname",
      "avatar_hash": "avatar_hash",
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
  "rejoined": 1,
  "rejoin_deferred": 0,
  "left": 0,
  "linked": 3,
  "departures_evaluated": false,
  "departures_refused": "",
  "departures_skipped": 0,
  "total_received": 400,
  "total_active": 400
}
```

`left` is always 0 and `departures_refused` always empty here: the push never marks anybody as left (see [The bot push](#the-bot-push-fallback)).

`observed_at` is optional: an ISO-8601 UTC time when the bot read the member list. A listed member whose departure was recorded at or after it keeps it and is counted in `rejoin_deferred` (see [A list older than the departure](#a-list-older-than-the-departure)).

## Troubleshooting

### `sync_guild_members` shows Failed, or a "holding back sign-outs" ticket is open

The scheduled sync refused to sign people out (see [Safety limits](#safety-limits)). The members it received were saved; the ticket and the task's traceback give the counts. Confirm the numbers in Discord, then run the task from `/site/config/background_tasks/` with **Accept a mass departure** ticked, or without it if the list was short by mistake.

### A rider who left still has access

Check `/team/discord-review/`. If their row is not marked as left, the bot's leave report did not arrive: look in Logfire for "Bot-reported member departure handled" or a 4xx on `/api/dbot/member_left/`. Run `sync_guild_members` from `/site/config/background_tasks/` to catch it now; if that run holds departures back, see the entry above. Staff and superusers keep access by design.

### /sync_members command not working

1. Ensure the bot has Server Members Intent enabled
2. Check bot has permission to read guild members
3. Verify `DBOT_AUTH_KEY` and `GUILD_ID` are configured

### GuildMember not linked to User

The `discord_id` on the User must match the GuildMember's `discord_id`. This happens automatically when users log in via Discord OAuth.
