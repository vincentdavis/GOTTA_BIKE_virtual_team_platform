# The Coalition Team Platform

A Django web application for The Coalition Zwift racing team. Manages team members, integrates with ZwiftPower and Zwift Racing APIs, and provides Discord bot integration.

## Features

- **Discord OAuth Authentication** - Login with Discord, requires guild membership
- **Profile Completion** - Warning banner encourages users to complete profile and verify Zwift
- **Zwift Account Verification** - Link and verify Zwift accounts
- **Race Ready Verification** - Weight/height/power verification for race eligibility
- **Membership Applications** - Discord-integrated application workflow for new members
- **Team Management** - Unified roster from ZwiftPower, Zwift Racing, and user data
- **Guild Member Sync** - Sync Discord members with Django
- **Data Connections** - Export team data to Google Sheets
- **Role-Based Permissions** - Discord role-based access control
- **Discord Bot API** - REST API for Discord bot integration

## Quick Start

```bash
# Install dependencies
uv sync

# Create environment file
cp .env.example .env

# Run migrations
uv run python manage.py migrate

# Install Tailwind
uv run python manage.py tailwind install

# Start dev server (Terminal 1)
uv run python manage.py runserver

# Start Tailwind watcher (Terminal 2)
uv run python manage.py tailwind start
```

## Documentation

Full documentation is available in the [docs/](docs/) folder:

- [Getting Started](docs/getting-started.md) - Setup, installation, environment variables, and commands
- [Authentication](docs/authentication.md) - Discord OAuth and guild membership
- [Profile Completion](docs/profile-completion.md) - User profile fields and warning banner
- [Permissions](docs/permissions.md) - Role-based access control via Discord roles
- [Race Ready Verification](docs/race-ready.md) - Weight/height/power verification system
- [Membership Applications](docs/membership-applications.md) - New member application workflow
- [Guild Member Sync](docs/guild-sync.md) - Discord member synchronization
- [Discord Bot](docs/discord-bot.md) - Slash commands and cogs
- [API Reference](docs/api.md) - Discord Bot API and Cron API

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Django 6.0, Python 3.14 |
| Frontend | Tailwind CSS 4.x, DaisyUI 5.x, HTMX |
| Database | PostgreSQL (prod), SQLite (dev) |
| Server | Granian WSGI |
| Package Manager | uv |
| Authentication | django-allauth with Discord OAuth |
| API | Django Ninja |
| Observability | Logfire |

## Project Structure

```
GOTTA_BIKE_virtual_team_platform/
├── apps/                   # Django applications
│   ├── accounts/           # User model, auth, permissions
│   ├── team/               # Team management, verification
│   ├── zwiftpower/         # ZwiftPower API integration
│   ├── zwiftracing/        # Zwift Racing API integration
│   ├── dbot_api/           # Discord bot REST API
│   └── data_connection/    # Google Sheets export
├── templates/              # Django templates
├── theme/                  # Tailwind CSS app
├── docs/                   # Documentation
├── gotta_bike_platform/    # Django project settings
└── manage.py
```

## Common Commands

```bash
uv run python manage.py runserver     # Dev server
uv run python manage.py migrate       # Apply migrations
uv run python manage.py db_worker     # Run background task worker
uv run pytest                         # Run tests
uv run ruff check .                   # Lint
```

## License

Private - The Coalition Zwift Racing Team
