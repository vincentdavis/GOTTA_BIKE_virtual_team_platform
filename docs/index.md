# The Coalition Team Platform Documentation

Welcome to the documentation for The Coalition Team Platform, a Django web application for managing a Zwift racing team.

## Quick Links

- [Getting Started](getting-started.md) - Setup, installation, and commands
- [Authentication](authentication.md) - Discord OAuth and guild membership
- [Profile Completion](profile-completion.md) - User profile fields and warning banner
- [Permissions](permissions.md) - Role-based access control
- [Race Ready Verification](race-ready.md) - Weight/height/power verification
- [Membership Applications](membership-applications.md) - New member application workflow
- [Guild Member Sync](guild-sync.md) - Discord member synchronization
- [Discord Bot](discord-bot.md) - Slash commands and cogs
- [API Reference](api.md) - Discord Bot API and Cron API

## Features

- **Discord OAuth Authentication** - Login with Discord, requires guild membership
- **Profile Completion** - Warning banner encourages users to complete their profile
- **Public User Profiles** - Team members can view each other's profiles (privacy-aware)
- **Zwift Account Verification** - Link and verify Zwift accounts
- **Race Ready Verification** - Submit and manage weight/height/power verification records
- **Membership Applications** - Discord-integrated application workflow for new members
- **Team Management** - Unified roster combining ZwiftPower, Zwift Racing, and user data
- **Guild Member Sync** - Sync Discord guild members with Django to compare membership vs accounts
- **Data Connections** - Export team data to Google Sheets with configurable fields and filters
- **Role-Based Permissions** - Team captains, admins, and members with different access levels
- **Discord Bot API** - REST API for Discord bot integration
- **Dynamic Settings** - Runtime-configurable settings via Django admin
- **CMS Pages** - Dynamic content pages with markdown, hero sections, and card layouts
- **Observability** - Logfire integration for logging and monitoring

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Django 6.0, Python 3.14 |
| Frontend | Tailwind CSS 4.x, DaisyUI 5.x, HTMX |
| Database | PostgreSQL (production), SQLite (development) |
| Server | Granian WSGI |
| Package Manager | uv |
| Authentication | django-allauth with Discord OAuth |
| API | Django Ninja |
| Observability | Logfire |

## Project Structure

```
GOTTA_BIKE_virtual_team_platform/
├── apps/
│   ├── accounts/       # User model, auth, permissions, middleware
│   ├── cms/            # Dynamic CMS pages with markdown support
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
├── docs/               # Documentation (you are here)
├── Dockerfile
├── entrypoint.sh
└── manage.py
```
