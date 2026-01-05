# Authentication

The platform uses Discord OAuth for authentication via django-allauth. There is no username/password login.

## Overview

- **Provider**: Discord OAuth only
- **Guild Membership Required**: Users must be a member of the configured Discord server to sign up or log in
- **OAuth Scopes**: `identify`, `email`, `guilds`
- **URLs**: `/accounts/` (login, logout, MFA management)

## How It Works

1. User clicks "Login with Discord"
2. User authorizes the app on Discord
3. App checks if user is a member of the required guild (via `GUILD_ID` setting)
4. If not a member, user is redirected to `DISCORD_URL` (invite link)
5. If member, user account is created/updated with Discord profile data
6. User is redirected to profile completion if profile is incomplete

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
| `zwid_verified` | Whether Zwift account has been verified |

## Custom Adapter

The `DiscordSocialAccountAdapter` (`apps/accounts/adapters.py`) handles:

1. **Guild Membership Check**: Verifies user is in the required Discord server before allowing login/signup
2. **Profile Population**: Syncs Discord profile data on signup
3. **Login Updates**: Updates Discord fields on every login (in case they changed)
4. **Login Redirect**: Redirects users with incomplete profiles to the profile edit page

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
| `GUILD_ID` | Discord server ID (required for membership check) |
| `GUILD_NAME` | Server name (shown in error messages) |
| `DISCORD_URL` | Invite link (users redirected here if not in guild) |

## Two-Factor Authentication (MFA)

The platform supports TOTP-based two-factor authentication via `allauth.mfa`:

- Setup at `/accounts/2fa/`
- 10 recovery codes generated
- 30-second TOTP period, 6 digits

## Troubleshooting

### "You must be a member of X Discord server"

The user is not in the Discord server specified by `GUILD_ID`. They need to:
1. Join the Discord server using the invite link
2. Try logging in again

### User can login but has no permissions

Discord roles may not be synced. The user should:
1. Run `/sync_my_roles` in Discord
2. Or an admin can run `/sync_roles` to sync all roles
