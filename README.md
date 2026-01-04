# The Coalition Team App

A Django web application for The Coalition Zwift racing team. Manages team members, integrates with ZwiftPower and Zwift
Racing APIs, and provides Discord bot integration.

## Features

- **Discord OAuth Authentication** - Login with Discord, requires guild membership
- **Zwift Account Verification** - Link and verify Zwift accounts
- **Race Ready Verification** - Submit and manage weight/height/power verification records
- **Team Management** - Unified roster combining ZwiftPower, Zwift Racing, and user data
- **Guild Member Sync** - Sync Discord guild members with Django to compare membership vs accounts
- **Data Connections** - Export team data to Google Sheets with configurable fields and filters
- **Role-Based Permissions** - Team captains, admins, and members with different access levels
- **Discord Bot API** - REST API for Discord bot integration
- **Dynamic Settings** - Runtime-configurable settings via Django admin

## Tech Stack

- **Backend**: Django 6.0, Python 3.14
- **Frontend**: Tailwind CSS 4.x, DaisyUI 5.x, HTMX
- **Database**: PostgreSQL (production), SQLite (development)
- **Server**: Granian WSGI
- **Package Manager**: uv
- **Authentication**: django-allauth with Discord OAuth
- **API**: Django Ninja

## Quick Start

### Prerequisites

- Python 3.14+
- [uv](https://github.com/astral-sh/uv) package manager
- Node.js (for Tailwind CSS development)

### Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd GOTTA_BIKE_virtual_team_platform
   ```

2. **Install dependencies**
   ```bash
   uv sync
   ```

3. **Create environment file**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

4. **Run migrations**
   ```bash
   uv run python manage.py migrate
   ```

5. **Install Tailwind CSS**
   ```bash
   uv run python manage.py tailwind install
   ```

6. **Start development server**
   ```bash
   # Terminal 1: Django server
   uv run python manage.py runserver

   # Terminal 2: Tailwind watcher
   uv run python manage.py tailwind start
   ```

## Environment Variables

### Required

| Variable         | Description            |
|------------------|------------------------|
| `SECRET_KEY`     | Django secret key      |
| `ZWIFT_USERNAME` | Zwift account email    |
| `ZWIFT_PASSWORD` | Zwift account password |
| `ZRAPP_API_URL`  | Zwift Racing API URL   |
| `ZRAPP_API_KEY`  | Zwift Racing API key   |

### Optional

| Variable                | Description                        |
|-------------------------|------------------------------------|
| `DEBUG`                 | Enable debug mode (default: False) |
| `DATABASE_URL`          | Database connection string         |
| `DISCORD_CLIENT_ID`     | Discord OAuth client ID            |
| `DISCORD_CLIENT_SECRET` | Discord OAuth client secret        |

### Django Admin Settings (constance)

The following settings are configured via Django admin at `/admin/constance/config/`:

| Setting                        | Description                                                 |
|--------------------------------|-------------------------------------------------------------|
| `GUILD_ID`                     | Discord guild/server ID (required for login)                |
| `GUILD_NAME`                   | Discord server name (shown in error messages)               |
| `DISCORD_URL`                  | Discord invite link (users redirected here if not in guild) |
| `ZWIFTPOWER_TEAM_ID`           | ZwiftPower team ID                                          |
| `DBOT_AUTH_KEY`                | Discord bot API authentication key                          |
| `RACE_READY_ROLE_ID`           | Discord role ID assigned when user is race ready (0=off)    |
| `PERM_*_ROLES`                 | Permission mappings (JSON arrays of Discord role IDs)       |
| `TEAM_NAME`                    | Name of the team                                            |
| `GOOGLE_SERVICE_ACCOUNT_EMAIL` | Google service account email for Sheets API                 |
| `GOOGLE_DRIVE_FOLDER_ID`       | Shared folder ID where spreadsheets are created             |

**Note**: Users must be a member of the Discord server specified by `GUILD_ID` to sign up or log in. If `GUILD_ID` is 0
or not set, the membership check is skipped.

## Development Commands

```bash
# Package management
uv sync                              # Install dependencies
uv add <package>                     # Add production dependency
uv add --dev <package>               # Add dev dependency

# Django
uv run python manage.py runserver    # Dev server
uv run python manage.py migrate      # Apply migrations
uv run python manage.py createsuperuser  # Create admin user

# Tailwind CSS
uv run python manage.py tailwind start   # Dev mode with watch
uv run python manage.py tailwind build   # Production build

# Testing & Linting
uv run pytest                        # Run tests
uv run ruff check .                  # Lint
uv run ruff check . --fix            # Lint and fix
uv run ruff format .                 # Format code

# Background Tasks
uv run python manage.py db_worker    # Run task worker
```

## Deployment

### Build and Deploy

```bash
# Build CSS and deploy
./build_and_deploy.sh
```

Or manually:

```bash
uv run python manage.py tailwind build
git add -f theme/static/css/dist/styles.css
git commit -m "Build CSS"
git push
```

### Docker

```bash
docker build -t coalition-app .
docker run --env-file .env -p 8000:8000 coalition-app
```

## Project Structure

```
GOTTA_BIKE_virtual_team_platform/
├── apps/
│   ├── accounts/       # User model, auth, roles
│   ├── data_connection/ # Google Sheets data export
│   ├── dbot_api/       # Discord bot REST API
│   ├── magic_links/    # Passwordless auth (legacy)
│   ├── team/           # Team management, verification records
│   ├── zwift/          # Zwift integration
│   ├── zwiftpower/     # ZwiftPower API integration
│   └── zwiftracing/    # Zwift Racing API integration
├── templates/          # Django templates
├── theme/              # Tailwind CSS app
├── gotta_bike_platform/
│   ├── config.py       # Pydantic settings
│   ├── settings.py     # Django settings
│   └── urls.py         # URL routing
├── Dockerfile
├── entrypoint.sh
└── manage.py
```

## Permissions

Permissions are granted via Discord roles configured in Django admin (Constance settings). The system checks:

1. **Superusers** always have all permissions
2. **Manual overrides** in `User.permission_overrides` (explicit grant/revoke)
3. **Discord roles** matched against Constance permission settings
4. **Legacy app roles** in `User.roles` (backward compatibility)

### Available Permissions

- `app_admin` - Full application admin
- `team_captain` - Can verify/reject race ready records
- `vice_captain` - Can view (not verify) race ready records
- `link_admin` - Can create, edit and delete team links
- `membership_admin` - Membership management
- `racing_admin` - Racing management
- `team_member` - Required for most pages; without it users can only see index and their profile

### Configuration

Configure permission mappings in Django admin at `/admin/constance/config/`:

- `PERM_APP_ADMIN_ROLES` - JSON array of Discord role IDs, e.g., `["1234567890123456789"]`
- `PERM_TEAM_CAPTAIN_ROLES`, `PERM_VICE_CAPTAIN_ROLES`, etc.

## Race Ready Verification

Users achieve "Race Ready" status by completing verification requirements, which gates participation in official team
races.

### Requirements

A user is race ready when they have BOTH:

1. **Weight (Full) verification** - Verified and not expired
2. **Height verification** - Verified and not expired

### Verification Flow

1. User submits a verification record (weight, height, or power photo)
2. Team captains review and verify/reject records
3. Verified records expire based on configurable timeframes

### Race Ready Discord Role

When a user achieves race ready status, the Discord bot automatically assigns them a configured role:

1. Configure `RACE_READY_ROLE_ID` in Django admin (set to `0` to disable)
2. Role is assigned/removed when users run `/my_profile` or `/sync_my_roles`
3. The bot requires `Manage Roles` permission and the role must be below the bot's highest role

### Team Roster

View race ready status at `/team/roster/` with filtering options. Export to Google Sheets via Data Connections.

## Guild Member Sync

Syncs Discord guild members with Django to track and compare membership status.

### How to Sync

1. Use the `/sync_members` slash command in Discord (admin only)
2. The bot sends all guild members to the Django API
3. View results in Django Admin > Accounts > Guild Members

### Comparison View

Access at `/admin/accounts/guildmember/comparison/` to see:

- **Guild Only** - Discord members who haven't created an account
- **Linked** - Discord members with linked accounts
- **Left Guild** - Users who left Discord but have accounts
- **Discord Users (No Guild)** - OAuth users without a guild record

**Note**: Regular Django accounts (staff/admin without Discord login) are not affected by the sync.

### Discord Bot Setup

The bot requires the **Server Members Intent** (privileged intent):

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Select your bot > Bot > Privileged Gateway Intents
3. Enable **Server Members Intent**

## License

Private - The Coalition Zwift Racing Team
