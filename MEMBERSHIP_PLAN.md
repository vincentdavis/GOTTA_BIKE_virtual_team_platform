# MembershipApplication Feature Plan

## Overview

Create a membership application system to process new member applications and onboarding. When approved, users can login
to the app with Discord OAuth2.

**Discord Bot Integration:** The Discord bot already has a `join_the_coalition` cog with a modal that collects initial
application data. When submitted, the bot will POST to the API to create a `MembershipApplication` record. The modal
form data is stored in `modal_form_data` JSONField for reference.

## 1. Model Design

### MembershipApplication Model (`apps/team/models.py`)

```python
class MembershipApplication(models.Model):
    """Membership application submitted via Discord modal."""

    class Status(models.TextChoices):
        """Application status choices."""

        PENDING = "pending", "Pending Review"
        IN_PROGRESS = "in_progress", "In Progress"
        WAITING_RESPONSE = "waiting_response", "Waiting for User Response"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    # Primary key - UUID for secure anonymous access
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Discord identity fields
    discord_id = models.CharField(max_length=20, unique=True, db_index=True)
    discord_username = models.CharField(max_length=100)
    server_nickname = models.CharField(max_length=100, blank=True)
    avatar_url = models.URLField(max_length=500, blank=True)
    guild_avatar_url = models.URLField(max_length=500, blank=True)

    # Raw Discord data (for reference/debugging)
    discord_user_data = models.JSONField(default=dict, blank=True)
    discord_member_data = models.JSONField(default=dict, blank=True)
    modal_form_data = models.JSONField(default=dict, blank=True)  # This is from the current bot join_the_coalition cog

    # Applicant-editable fields
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    agree_privacy = models.BooleanField(default=False)
    agree_tos = models.BooleanField(default=False)
    applicant_notes = models.TextField(blank=True)

    # Admin-only fields
    admin_notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # Timestamps
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)

    # Modified by tracking
    modified_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="modified_applications",
    )

    class Meta:
        """Meta options for MembershipApplication."""

        ordering: ClassVar[list[str]] = ["-date_created"]
        verbose_name = "Membership Application"
        verbose_name_plural = "Membership Applications"

    def __str__(self) -> str:
        """Return string representation."""
        return f"{self.discord_username} - {self.get_status_display()}"

    @property
    def display_name(self) -> str:
        """Return the best display name available."""
        return self.server_nickname or self.discord_username

    @property
    def full_name(self) -> str:
        """Return full name if available."""
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.first_name or self.last_name or ""

    @property
    def is_complete(self) -> bool:
        """Check if applicant has completed required fields."""
        return bool(
            self.first_name
            and self.last_name
            and self.agree_privacy
            and self.agree_tos
        )

    @property
    def is_pending(self) -> bool:
        """Check if application is pending review."""
        return self.status == self.Status.PENDING

    @property
    def is_actionable(self) -> bool:
        """Check if application can be approved/rejected."""
        return self.status not in [self.Status.APPROVED, self.Status.REJECTED]
```

## 2. URL Structure

### URLs (`apps/team/urls.py`)

```python
# Membership Application URLs (admin only)
path("applications/", views.membership_application_list_view, name="application_list"),
path("applications/<uuid:pk>/", views.membership_application_admin_view, name="application_admin"),

# Public application URL (no login required - accessed via UUID link from Discord)
path("apply/<uuid:pk>/", views.membership_application_public_view, name="application_public"),
```

## 3. Views

### 3.1 Admin List View (`membership_application_list_view`)

**Access:** Users with `membership_admin` permission only.

**Features:**

- Filter by status (pending, in_progress, waiting_response, approved, rejected)
- Search by discord_username, server_nickname, first_name, last_name
- Sort by date_created, date_modified, status
- Show count per status

**URL:** `/team/applications/`

### 3.2 Admin Detail View (`membership_application_admin_view`)

**Access:** Users with `membership_admin` permission only.

**Features:**

- View all fields including Discord data JSONFields
- Edit admin_notes and status
- Action buttons: Approve, Reject, Request Info
- Show modification history (modified_by, date_modified)
- Link to Discord profile

**URL:** `/team/applications/<uuid>/`

### 3.3 Applicant View (`membership_application_public_view`)

**Access:** Public (no login required) - accessed via UUID link

**Features:**

- View-only: server_nickname, status
- Editable: first_name, last_name, agree_privacy, agree_tos, applicant_notes
- Cannot edit if status is approved or rejected
- Show status badge (pending, approved, rejected, etc.)
- Form submission updates the record

**URL:** `/team/apply/<uuid>/`

## 4. Forms

### MembershipApplicationApplicantForm

For applicants to complete their application:

```python
class MembershipApplicationApplicantForm(forms.ModelForm):
    """Form for applicants to complete their membership application."""

    class Meta:
        model = MembershipApplication
        fields = ["first_name", "last_name", "agree_privacy", "agree_tos", "applicant_notes"]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "last_name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "agree_privacy": forms.CheckboxInput(attrs={"class": "checkbox checkbox-primary"}),
            "agree_tos": forms.CheckboxInput(attrs={"class": "checkbox checkbox-primary"}),
            "applicant_notes": forms.Textarea(attrs={"class": "textarea textarea-bordered w-full", "rows": 4}),
        }

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("agree_privacy"):
            self.add_error("agree_privacy", "You must agree to the privacy policy.")
        if not cleaned_data.get("agree_tos"):
            self.add_error("agree_tos", "You must agree to the terms of service.")
        return cleaned_data
```

### MembershipApplicationAdminForm

For admins to update status and notes:

```python
class MembershipApplicationAdminForm(forms.ModelForm):
    """Form for admins to update application status and notes."""

    class Meta:
        model = MembershipApplication
        fields = ["status", "admin_notes"]
        widgets = {
            "status": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "admin_notes": forms.Textarea(attrs={"class": "textarea textarea-bordered w-full", "rows": 4}),
        }
```

## 5. Discord Bot API Endpoint

### Create Application Endpoint (`apps/dbot_api/api.py`)

```python
class MembershipApplicationCreateSchema(Schema):
    """Schema for creating a membership application."""

    discord_id: str
    discord_username: str
    server_nickname: str = ""
    avatar_url: str = ""
    guild_avatar_url: str = ""
    discord_user_data: dict = {}
    discord_member_data: dict = {}
    modal_form_data: dict = {}
    first_name: str = ""
    last_name: str = ""
    applicant_notes: str = ""


class MembershipApplicationResponseSchema(Schema):
    """Schema for membership application response."""

    id: str  # UUID as string
    discord_id: str
    discord_username: str
    status: str
    application_url: str
    is_complete: bool
    date_created: str


@api.post("/membership_application")
def create_membership_application(request, payload: MembershipApplicationCreateSchema) -> dict:
    """Create a new membership application from Discord modal."""
    # Check if application already exists for this discord_id
    existing = MembershipApplication.objects.filter(discord_id=payload.discord_id).first()
    if existing:
        return {
            "id": str(existing.id),
            "discord_id": existing.discord_id,
            "discord_username": existing.discord_username,
            "status": existing.status,
            "application_url": f"/team/apply/{existing.id}/",
            "is_complete": existing.is_complete,
            "date_created": existing.date_created.isoformat(),
            "already_exists": True,
        }

    # Create new application
    application = MembershipApplication.objects.create(
        discord_id=payload.discord_id,
        discord_username=payload.discord_username,
        server_nickname=payload.server_nickname,
        avatar_url=payload.avatar_url,
        guild_avatar_url=payload.guild_avatar_url,
        discord_user_data=payload.discord_user_data,
        discord_member_data=payload.discord_member_data,
        modal_form_data=payload.modal_form_data,
        first_name=payload.first_name,
        last_name=payload.last_name,
        applicant_notes=payload.applicant_notes,
    )

    return {
        "id": str(application.id),
        "discord_id": application.discord_id,
        "discord_username": application.discord_username,
        "status": application.status,
        "application_url": f"/team/apply/{application.id}/",
        "is_complete": application.is_complete,
        "date_created": application.date_created.isoformat(),
        "already_exists": False,
    }


@api.get("/membership_application/{discord_id}")
def get_membership_application(request, discord_id: str) -> dict:
    """Get membership application by Discord ID."""
    try:
        application = MembershipApplication.objects.get(discord_id=discord_id)
        return {
            "id": str(application.id),
            "discord_id": application.discord_id,
            "discord_username": application.discord_username,
            "status": application.status,
            "application_url": f"/team/apply/{application.id}/",
            "is_complete": application.is_complete,
            "date_created": application.date_created.isoformat(),
        }
    except MembershipApplication.DoesNotExist:
        return api.create_response(request, {"error": "Application not found"}, status=404)
```

## 6. Templates

### 6.1 Application List (Admin) - `templates/team/application_list.html`

```
- Header: "Membership Applications"
- Status count badges (Pending: X, In Progress: X, etc.)
- Filter form:
  - Search input (q)
  - Status dropdown
  - Sort dropdown (date_created, date_modified)
  - Direction toggle (asc/desc)
- Table columns:
  - Avatar (small)
  - Discord Username
  - Server Nickname
  - Full Name
  - Status (badge)
  - Date Created
  - Date Modified
  - Actions (View)
```

### 6.2 Application Admin Detail - `templates/team/application_admin.html`

```
- Back to list link
- Header with avatar and display name
- Status badge (large)

- Card: Discord Information
  - Discord ID
  - Username
  - Server Nickname
  - Avatar links
  - Raw Discord data (collapsible)

- Card: Applicant Information
  - First Name, Last Name
  - Applicant Notes
  - Privacy/TOS agreement status
  - Application complete status

- Card: Admin Actions (form)
  - Status dropdown
  - Admin Notes textarea
  - Save button
  - Quick action buttons: Approve, Reject, Request Info

- Card: History
  - Date Created
  - Date Modified
  - Modified By
```

### 6.3 Application Public Form - `templates/team/application_public.html`

```
- Header: "Complete Your Application"
- Status badge (current status)
- Server Nickname (read-only display)

- If status is approved:
  - Success message with link to login

- If status is rejected:
  - Rejection message

- If status allows editing:
  - Form:
    - First Name input
    - Last Name input
    - Privacy policy checkbox with link (from PRIVACY_POLICY_URL)
    - Terms of service checkbox with link (from TERMS_OF_SERVICE_URL)
    - Applicant Notes textarea
    - Submit button

- Status timeline/progress indicator (optional)
```

## 7. Constance Settings

**Already exist in `settings.py` CONSTANCE_CONFIG (no changes needed):**

- `PERM_MEMBERSHIP_ADMIN_ROLES` - Discord role IDs for membership admin permission
- `PRIVACY_POLICY_URL` - URL to Privacy Policy page
- `TERMS_OF_SERVICE_URL` - URL to Terms of Service page

**Need to add:**

```python
"WELCOME_TEAM_CHANNEL_ID": (0, "Discord channel ID for welcome/application messages", int),
```

Add to the "Discord Settings" fieldset group.

## 8. Admin Registration

```python
@admin.register(MembershipApplication)
class MembershipApplicationAdmin(admin.ModelAdmin):
    """Admin configuration for MembershipApplication."""

    list_display = [
        "discord_username",
        "server_nickname",
        "full_name",
        "status",
        "is_complete",
        "date_created",
        "date_modified",
    ]
    list_filter = ["status", "agree_privacy", "agree_tos"]
    search_fields = ["discord_id", "discord_username", "server_nickname", "first_name", "last_name"]
    readonly_fields = [
        "id",
        "discord_id",
        "discord_username",
        "discord_user_data",
        "discord_member_data",
        "modal_form_data",
        "date_created",
        "date_modified",
    ]
    fieldsets = [
        ("Application ID", {"fields": ["id"]}),
        ("Discord Identity", {"fields": [
            "discord_id",
            "discord_username",
            "server_nickname",
            "avatar_url",
            "guild_avatar_url",
        ]}),
        ("Applicant Information", {"fields": [
            "first_name",
            "last_name",
            "agree_privacy",
            "agree_tos",
            "applicant_notes",
        ]}),
        ("Admin", {"fields": ["status", "admin_notes", "modified_by"]}),
        ("Raw Data", {"fields": [
            "discord_user_data",
            "discord_member_data",
            "modal_form_data",
        ], "classes": ["collapse"]}),
        ("Timestamps", {"fields": ["date_created", "date_modified"]}),
    ]
```

## 9. Implementation Steps

### Phase 1: Model & Migration

1. [x] Add MembershipApplication model to `apps/team/models.py`
2. [x] Create and apply migration (`0004_membershipapplication.py`)
3. [x] Register model in admin (`apps/team/admin.py`)

### Phase 2: Forms

4. [x] Create MembershipApplicationApplicantForm in `apps/team/forms.py`
5. [x] Create MembershipApplicationAdminForm in `apps/team/forms.py`

### Phase 3: Views

6. [x] Implement `membership_application_list_view` (admin list)
7. [x] Implement `membership_application_admin_view` (admin detail/edit)
8. [x] Implement `membership_application_public_view` (public applicant form)

### Phase 4: URLs

9. [x] Add URL patterns to `apps/team/urls.py`

### Phase 5: Templates

10. [x] Create `templates/team/application_list.html`
11. [x] Create `templates/team/application_admin.html`
12. [x] Create `templates/team/application_public.html`

### Phase 6: Discord Bot API

13. [x] Add schemas to `apps/dbot_api/api.py`
14. [x] Implement `create_membership_application` endpoint
15. [x] Implement `get_membership_application` endpoint

### Phase 7: Constance Settings

16. [x] Add `WELCOME_TEAM_CHANNEL_ID` to `settings.py` CONSTANCE_CONFIG (in "Discord Guild" fieldset)

### Phase 8: Navigation & Integration

17. [x] Add link to application list in team navigation (for membership admins) - `theme/templates/header.html`
18. [ ] Test full flow: Discord `join_the_coalition` modal → API → public form → admin review → approval

## 10. Security Considerations

1. **UUID-based URLs**: Applications are accessed via UUID, making URLs unguessable
2. **No authentication required for public form**: Applicants don't have accounts yet
3. **Permission checks**: Admin views require `membership_admin` permission
4. **Rate limiting**: Consider adding rate limiting to API endpoint
5. **Data validation**: Validate Discord ID format, sanitize text inputs
6. **CSRF protection**: Public form still needs CSRF token (use `{% csrf_token %}`)

## 11. Future Enhancements (Out of Scope)

- Email notifications when status changes
- Automatic approval based on criteria
- Application expiration/cleanup
- Bulk actions (approve/reject multiple)
- Application statistics dashboard
- Integration with GuildMember sync
