# Membership Applications

The membership application system allows prospective team members to apply via Discord and complete their application through the web platform.

## Overview

New members submit an application through a Discord modal, then receive a unique link to complete their application on the web platform. Membership admins can review, update status, and approve/reject applications.

## Application Workflow

```
┌─────────────────────────┐     ┌─────────────────────────┐
│  Discord Modal Submit   │────▶│  Application Created    │
│  (join_the_coalition)   │     │  Status: Pending        │
└─────────────────────────┘     └────────────┬────────────┘
                                             │
                                             ▼
                                ┌─────────────────────────┐
                                │  User Receives DM       │
                                │  with Application Link  │
                                └────────────┬────────────┘
                                             │
                                             ▼
                                ┌─────────────────────────┐
                                │  User Completes Form    │
                                │  (name, agreements)     │
                                └────────────┬────────────┘
                                             │
                                             ▼
┌─────────────────────────┐     ┌─────────────────────────┐
│       Rejected          │◀────│  Admin Reviews          │────▶│       Approved          │
│                         │     │  (membership_admin)     │     │                         │
└─────────────────────────┘     └─────────────────────────┘     └─────────────────────────┘
```

## Application Status

| Status | Description |
|--------|-------------|
| `pending` | Awaiting review |
| `in_progress` | Admin is reviewing |
| `waiting_response` | Waiting for user to provide additional info |
| `approved` | Application approved, user can join |
| `rejected` | Application rejected |

## MembershipApplication Model

### Discord Identity Fields

| Field | Description |
|-------|-------------|
| `discord_id` | Discord user ID (unique) |
| `discord_username` | Discord username at time of application |
| `server_nickname` | Server nickname at time of application |
| `avatar_url` | Discord avatar URL |
| `guild_avatar_url` | Server-specific avatar URL |

### Applicant Profile Fields

| Field | Description |
|-------|-------------|
| `first_name` | Applicant's first name |
| `last_name` | Applicant's last name |
| `zwift_id` | Zwift account ID |
| `country` | Country of residence |
| `timezone` | User's timezone |
| `birth_year` | Year of birth |
| `gender` | Gender (male/female/other) |
| `unit_preference` | Metric or Imperial |

### Equipment Fields

| Field | Description |
|-------|-------------|
| `trainer` | Primary trainer |
| `power_meter` | Power meter |
| `dual_recording` | Dual recording preference (none/trainer/powermeter) |

### Agreement Fields

| Field | Description |
|-------|-------------|
| `agree_privacy` | Agreed to privacy policy |
| `agree_tos` | Agreed to terms of service |
| `applicant_notes` | Notes from applicant |

### Admin Fields

| Field | Description |
|-------|-------------|
| `admin_notes` | Internal notes from admins |
| `status` | Application status |
| `modified_by` | Admin who last modified |

## API Endpoints

### POST /api/dbot/membership_application

Create a new membership application from Discord modal.

**Body:**
```json
{
  "discord_id": "123456789",
  "discord_username": "user#1234",
  "server_nickname": "Nickname",
  "avatar_url": "https://cdn.discordapp.com/...",
  "modal_form_data": {}
}
```

**Response:**
```json
{
  "id": "uuid",
  "discord_id": "123456789",
  "application_url": "https://domain.com/team/apply/uuid/",
  "status": "pending",
  "already_exists": false
}
```

If an application already exists for the Discord ID, returns the existing application with `already_exists: true`.

### GET /api/dbot/membership_application/{discord_id}

Get membership application by Discord ID.

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

## Web App URLs

| URL | Description | Access |
|-----|-------------|--------|
| `/team/apply/{uuid}/` | Public application form | Anyone with link |
| `/team/applications/` | Application list | Membership admins |
| `/team/applications/{uuid}/` | Admin review page | Membership admins |

## Permissions

| Action | Required Permission |
|--------|---------------------|
| Submit application | Anyone (via Discord) |
| View own application | Anyone with UUID link |
| View all applications | `membership_admin` |
| Update application status | `membership_admin` |
| Add admin notes | `membership_admin` |

Configure `PERM_MEMBERSHIP_ADMIN_ROLES` in Django admin with Discord role IDs that should have membership admin access.

## Discord Bot Integration

The Discord bot's `join_the_coalition` cog handles the initial application submission:

1. User triggers the join modal (button or command)
2. User fills out the Discord modal form
3. Bot POSTs to `/api/dbot/membership_application`
4. Bot sends DM to user with the application URL
5. User completes application on the web platform

## Model Properties

| Property | Returns | Description |
|----------|---------|-------------|
| `display_name` | str | Server nickname or Discord username |
| `full_name` | str | Combined first and last name |
| `is_complete` | bool | True if required fields are filled |
| `is_pending` | bool | True if status is pending |
| `is_actionable` | bool | True if can be approved/rejected |
| `is_editable` | bool | True if applicant can still edit |
