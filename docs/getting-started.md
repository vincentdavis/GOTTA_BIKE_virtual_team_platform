# Getting Started

## Prerequisites

- Python 3.14+
- [uv](https://github.com/astral-sh/uv) package manager
- Node.js (for Tailwind CSS development)

## Installation

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

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Django secret key |

### Optional

| Variable | Description |
|----------|-------------|
| `DEBUG` | Enable debug mode (default: False) |
| `DATABASE_URL` | Database connection string |
| `DISCORD_CLIENT_ID` | Discord OAuth client ID |
| `DISCORD_CLIENT_SECRET` | Discord OAuth client secret |
| `LOGFIRE_TOKEN` | Logfire API token (optional) |
| `LOGFIRE_ENVIRONMENT` | Environment name (e.g., "production") |

### Django Admin Settings (Constance)

Most settings are configured via Django admin at `/admin/constance/config/`:

| Setting | Description |
|---------|-------------|
| `GUILD_ID` | Discord guild/server ID (required for login) |
| `GUILD_NAME` | Discord server name (shown in error messages) |
| `DISCORD_URL` | Discord invite link (users redirected here if not in guild) |
| `ZWIFTPOWER_TEAM_ID` | ZwiftPower team ID |
| `ZWIFT_USERNAME` | Zwift account email for API access |
| `ZWIFT_PASSWORD` | Zwift account password for API access |
| `DBOT_AUTH_KEY` | Discord bot API authentication key |
| `ZRAPP_API_URL` | Zwift Racing API base URL |
| `ZRAPP_API_KEY` | Zwift Racing API key |
| `RACE_READY_ROLE_ID` | Discord role ID assigned when user is race ready (0=off) |
| `PERM_*_ROLES` | Permission mappings (JSON arrays of Discord role IDs) |
| `GOOGLE_SERVICE_ACCOUNT_EMAIL` | Google service account email for Sheets API |
| `GOOGLE_DRIVE_FOLDER_ID` | Shared folder ID where spreadsheets are created |

**Note**: Users must be a member of the Discord server specified by `GUILD_ID` to sign up or log in. If `GUILD_ID` is 0 or not set, the membership check is skipped.

## Commands

### Package Management

```bash
uv sync                    # Install dependencies
uv add <package>           # Add production dependency
uv add --dev <package>     # Add dev dependency
```

### Django

```bash
uv run python manage.py runserver         # Dev server
uv run python manage.py check             # Validate config
uv run python manage.py makemigrations    # Create migrations
uv run python manage.py migrate           # Apply migrations
uv run python manage.py createsuperuser   # Create admin user
```

### Background Tasks

```bash
uv run python manage.py db_worker         # Run task worker
```

### Tailwind CSS

```bash
uv run python manage.py tailwind install  # Install npm deps
uv run python manage.py tailwind start    # Dev mode with watch
uv run python manage.py tailwind build    # Production build
```

### Testing & Linting

```bash
uv run pytest                   # Run tests
uv run pytest <path>::<test>    # Run single test
uv run ruff check .             # Lint
uv run ruff check . --fix       # Lint and fix
uv run ruff format .            # Format code
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

### Production Server

```bash
uv run granian gotta_bike_platform.wsgi:application --interface wsgi
```

## Observability (Logfire)

The application uses [Logfire](https://logfire.pydantic.dev/) for observability. Configure with environment variables:

- `LOGFIRE_TOKEN` - API token (optional; if not set, logs are local only)
- `LOGFIRE_ENVIRONMENT` - Environment name

Usage in code:

```python
import logfire

logfire.info("User logged in", user_id=user.id, discord_id=user.discord_id)
logfire.warning("Rate limit approaching", api="zwiftpower", remaining=5)
logfire.error("API request failed", error=str(e), endpoint=url)
```
