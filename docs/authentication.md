# Authentication

Riders sign in with Discord OAuth through django-allauth, and only members of the team's Discord server may. The one other login is Django's own username/password form at `/admin/`, for staff accounts such as the one `createsuperuser` or `ensuresuperuser` creates. allauth's own email- and password-based routes are closed (see [Closed allauth routes](#closed-allauth-routes)).

## Overview

- **Rider login**: Discord OAuth only
- **Staff login**: `/admin/` username and password (Django's `ModelBackend`), deliberately outside the guild rule
- **Guild membership required**: checked live when a rider signs in, and enforced afterwards from the guild sync (see [Leaving the Discord server](#leaving-the-discord-server))
- **OAuth scopes**: `identify`, `email`, `guilds`
- **URLs**: `/accounts/` (login, logout, account connections, MFA management)

## How It Works

1. User clicks "Continue with Discord" on the login page
2. User authorizes the app on Discord
3. Before writing anything, the app runs its checks in this order:
    - **Block list**: the incoming Discord id, and the existing account allauth matched (which can be matched by email alone), must not be blocked
    - **Guild membership**: a live call with the rider's own token must list `GUILD_ID` among their servers
    - **Verified email**: Discord must have verified the account's email
4. A failed check sends the user back to the login page with an error message. A user who is not in the server is told so, with a "Join here" link to `DISCORD_URL` when it is an `http(s)` URL
5. If every check passes, the account is created or its Discord fields are updated (never its profile fields), and any stale "left the server" stamp for that Discord id is cleared
6. User is redirected to profile completion if profile is incomplete

The guild check fails closed. A `GUILD_ID` of `0` refuses every Discord login instead of skipping the check, and so does a Discord error, timeout or rate limit.

## User Model Fields

The custom User model stores Discord-specific data:

| Field | Description |
|-------|-------------|
| `discord_id` | Discord user ID (snowflake) |
| `discord_username` | Discord username |
| `discord_nickname` | Discord server nickname or global display name |
| `discord_avatar` | Discord avatar hash |
| `discord_roles` | JSON mapping of `{role_id: role_name}` |
| `zwid` | Zwift user ID |
| `zwid_verified` | Whether the Zwift account has been verified. New verifications come only from the Zwift OAuth connection (zauth) at `/user/zauth/` |
| `zwid_verification_method` | How the current verification was obtained: `zauth` (the only way now), or the historical `legacy` and `admin` |

## Custom Adapters

Both live in `apps/accounts/adapters.py`.

`DiscordSocialAccountAdapter` handles Discord logins:

1. **Checks**: block list, live guild membership and verified email, all before any write
2. **Reconnect**: attaches a login to the account that already holds its `discord_id` when the `SocialAccount` row was lost (refused if more than one account holds the id)
3. **Profile Population**: syncs Discord profile data on signup
4. **Login Updates**: updates Discord fields on every login (in case they changed)
5. **Login Redirect**: redirects users with incomplete profiles to the profile edit page

`NoLocalSignupAccountAdapter` is the account adapter:

1. **No local signup**: an account can only come from a Discord login, so `/accounts/signup/` shows allauth's "sign up closed" page. Discord signup stays open because the social adapter says so explicitly
2. **Discord-only login**: refuses any allauth login that did not come through a Discord social login. This is a second line behind the closed routes. It does not run again when a login resumes after the TOTP step, and `/admin/` does not go through allauth at all

## Leaving the Discord Server

Discord confirms membership only when a rider signs in, but a session outlives that check. The guild sync closes the gap.

- **Sign-out**: the guild sync (`sync_guild_members`, every `SCHEDULER_SYNC_GUILD_MEMBERS_HOURS`, default 6) stamps `GuildMember.date_left` for any member it no longer lists. `DepartedMemberLogoutMiddleware` then signs that user out on their next request and shows a warning. The request carries on as anonymous, so a protected page redirects to the login page. An HTMX request gets an `HX-Redirect` to the login page instead.
- **Lag**: a rider who leaves keeps access until the next sync runs, so for up to the sync interval.
- **Exempt**: staff and superusers are never signed out by this rule.
- **The rule** (`apps/accounts/membership.py:is_departed_member`): a `GuildMember` row for the user's *current* `discord_id` with `date_left` set.
- **Accounts with no row**: left alone. Local accounts (no `discord_id`) never get one. A Discord-linked account without one is left alone only until the next sync that passes its safety checks: that sync writes a departed row for every Discord-linked account its member list has never included. So someone who signs in and leaves before any sync has seen them is still caught.
- **Rejoining**: signing in with Discord again passes the live check and clears the stamp, so the rider is not signed out again while waiting for the next sync. A sync clears it too when it lists the rider.
- **API keys**: `user_can_use_api` (`apps/user_api/services.py`) refuses a departed user who is not staff, on every bearer request (401) and on the API key page. It is needed separately because the role syncs skip departed riders, so their stored `team_member` role never goes away. The keys are not deleted.
- **Safety**: the sync does not stamp departures from an empty member list or from a suspiciously large drop. See [Guild Member Sync](guild-sync.md).

### Outside the guild rule

These are deliberate, by owner decision:

- **`/admin/` login**: Django's own login view and `ModelBackend`, not allauth, so none of the Discord checks apply. It is meant for staff, who are also exempt from the sign-out above.
- **Magic links** (`/m/`): single-use links valid for 5 minutes, minted by the Discord bot's `/team_links` command (`GET /api/dbot/team_links`). A link signs its user in without a guild check. If that user has left the server, the middleware signs them out on the following request.

## Closed allauth Routes

With `ACCOUNT_LOGIN_METHODS = {"email"}` and no password field, allauth treated a bare email address as enough to start a login. A POST of an email address to `/accounts/login/` emailed a code, and `/accounts/login/code/confirm/` signed its holder in. Password reset and password set would likewise give a password to an account meant for Discord only. Every one of these was a way in that skipped the block list and the guild check, so `gotta_bike_platform/urls.py` shadows them ahead of the allauth include:

| Route | Behaviour |
|-------|-----------|
| `/accounts/login/` | GET and HEAD only (`discord_only_login`). The page offers Discord and nothing else; any other method is redirected back to it |
| `/accounts/login/code/` and every sub-path | 404 (`closed_account_route`) |
| `/accounts/password/` and every sub-path | 404 |
| `/accounts/email/` and every sub-path | 404 |
| `/accounts/confirm-email/` and every sub-path | 404 |
| `/accounts/3rdparty/signup/` | Redirected to the login page (`block_social_signup`) |

`SOCIALACCOUNT_ONLY` would drop these routes itself, but allauth refuses it alongside `allauth.mfa`.

allauth's URL names still reverse, so its own layout would link to the closed pages. `templates/allauth/layouts/base.html` is a copy of that layout without the "Change Email" and "Change Password" entries, and with the page language set. Compare it with the installed copy when upgrading allauth.

Guarded by `apps/accounts/test_email_login_routes_closed.py` and `apps/accounts/test_local_signup_closed.py`. The other rules above are guarded by `test_guild_membership_check.py`, `test_blocked_logins.py`, `test_departed_member_logout.py` and `test_guild_sync_safety.py` (all in `apps/accounts/`), and by `apps/user_api/test_api_key_auth.py`.

## Configuration

### Discord Developer Portal

1. Create an application at [Discord Developer Portal](https://discord.com/developers/applications)
2. Under OAuth2, add redirect URI: `https://your-domain.com/accounts/discord/login/callback/`
3. Copy Client ID and Client Secret

### Environment Variables

```bash
DISCORD_CLIENT_ID=your_client_id
DISCORD_CLIENT_SECRET=your_client_secret
```

### Constance Settings

Configure in Django admin at `/admin/constance/config/`:

| Setting | Description |
|---------|-------------|
| `GUILD_ID` | Discord server ID. Required: `0` refuses every Discord login |
| `GUILD_NAME` | Server name (shown in error messages) |
| `DISCORD_URL` | Invite link, offered as "Join here" on the login page to users who are not in the server (`http(s)` URLs only) |
| `SCHEDULER_SYNC_GUILD_MEMBERS_HOURS` | How often the guild sync runs, and so the longest a rider who left keeps access (default 6) |

Blocked Discord accounts are managed under "Blocked logins" at `/site/config/compliance/`.

## Two-Factor Authentication (MFA)

The platform supports TOTP-based two-factor authentication via `allauth.mfa`:

- Setup at `/accounts/2fa/`
- 10 recovery codes generated
- 30-second TOTP period, 6 digits

## Troubleshooting

### "You must be a member of the X Discord server to log in."

The user is not in the Discord server specified by `GUILD_ID`. They need to:
1. Join the Discord server using the invite link
2. Try logging in again

### "Sign-in is unavailable because the team's Discord server is not configured."

`GUILD_ID` is `0`. Set it to the server's ID. Until then, only the `/admin/` login signs anyone in (the bot API, and so its magic links, refuses a guild ID that does not match `GUILD_ID`).

### "Failed to verify Discord server membership. Please try again."

Discord could not be asked, or its answer could not be read. Look for "Failed to fetch Discord guilds" or "Failed to read Discord guilds" in Logfire.

### "This Discord account cannot sign in."

The Discord account, or the account it matched, is on the block list at `/site/config/compliance/`.

### "You have been signed out because you are no longer a member of the team's Discord server."

The guild sync has marked the user's Discord id as departed. If they have rejoined, signing in with Discord again clears it. If they never left, check their row on `/team/discord-review/` and the latest `sync_guild_members` run.

### User can login but has no permissions

Discord roles may not be synced. The user should:
1. Run `/sync_my_roles` in Discord
2. Or an admin can run `/sync_roles` to sync all roles
