# CMS Feature Plan

## Overview

Add basic CMS capabilities to create dynamic pages with markdown content, optional hero sections, and configurable card sections above/below content.

## URL Structure

```
/page/<slug>/  - Public page view
```

## Features

1. **Dynamic Pages** - Create pages with unique slugs
2. **Markdown Content** - All content uses markdown for editing
3. **Hero Section** - Optional hero with background image, title, subtitle
4. **Card Sections** - Optional card grids above and/or below main content
5. **Draft/Published Status** - Control page visibility

---

## Database Models

### New App: `apps/cms/`

#### Page Model

```python
class Page(models.Model):
    """CMS page with markdown content and optional sections."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    # Identity
    slug = models.SlugField(max_length=100, unique=True, db_index=True)
    title = models.CharField(max_length=200)

    # Content (Markdown)
    content = models.TextField(blank=True, help_text="Page content in Markdown format")

    # Hero Section (optional)
    hero_enabled = models.BooleanField(default=False)
    hero_image = models.ImageField(upload_to="cms/heroes/", blank=True, null=True)
    hero_title = models.CharField(max_length=200, blank=True)
    hero_subtitle = models.TextField(blank=True)

    # Card Sections (stored as JSON)
    cards_above = models.JSONField(
        default=list,
        blank=True,
        help_text="Cards displayed above content"
    )
    cards_below = models.JSONField(
        default=list,
        blank=True,
        help_text="Cards displayed below content"
    )

    # SEO
    meta_description = models.CharField(max_length=160, blank=True)

    # Status & Visibility
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT
    )
    require_login = models.BooleanField(
        default=False,
        help_text="Require user to be logged in to view"
    )
    require_team_member = models.BooleanField(
        default=False,
        help_text="Require team member permission to view"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_pages"
    )

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("cms:page_detail", kwargs={"slug": self.slug})

    @property
    def is_published(self):
        return self.status == self.Status.PUBLISHED
```

#### Card JSON Structure

Each card in `cards_above` and `cards_below` follows this structure:

```json
{
    "icon": "🚴",
    "title": "Card Title",
    "description": "Card description text (supports markdown)",
    "link_url": "/optional/link/",
    "link_text": "Learn More"
}
```

---

## Files to Create/Modify

### New Files

| File | Purpose |
|------|---------|
| `apps/cms/__init__.py` | App init |
| `apps/cms/apps.py` | App config |
| `apps/cms/models.py` | Page model |
| `apps/cms/admin.py` | Django admin configuration |
| `apps/cms/views.py` | Page view |
| `apps/cms/urls.py` | URL patterns |
| `apps/cms/forms.py` | Admin forms for card editing |
| `templates/cms/page_detail.html` | Page template |
| `templates/cms/partials/_hero.html` | Reusable hero partial |
| `templates/cms/partials/_cards.html` | Reusable cards partial |

### Modifications

| File | Change |
|------|--------|
| `gotta_bike_platform/settings.py` | Add `apps.cms` to INSTALLED_APPS |
| `gotta_bike_platform/urls.py` | Add CMS URL include |
| `theme/templates/sidebar.html` | Add CMS pages link (admin only) |

---

## Implementation Details

### 1. App Setup (`apps/cms/apps.py`)

```python
from django.apps import AppConfig

class CmsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cms"
    verbose_name = "CMS"
```

### 2. Views (`apps/cms/views.py`)

```python
@require_GET
def page_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Display a CMS page."""
    page = get_object_or_404(Page, slug=slug)

    # Check if published (or user is admin)
    if not page.is_published and not request.user.is_app_admin:
        raise Http404("Page not found")

    # Check permissions
    if page.require_login and not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())

    if page.require_team_member:
        if not request.user.is_authenticated or not request.user.is_team_member:
            raise PermissionDenied("Team member access required")

    # Render markdown content
    content_html = markdown.markdown(
        page.content,
        extensions=["extra", "codehilite", "toc"]
    )

    return render(request, "cms/page_detail.html", {
        "page": page,
        "content_html": content_html,
    })
```

### 3. URLs (`apps/cms/urls.py`)

```python
from django.urls import path
from apps.cms import views

app_name = "cms"

urlpatterns = [
    path("<slug:slug>/", views.page_detail, name="page_detail"),
]
```

### 4. Main URL Configuration (`gotta_bike_platform/urls.py`)

Add before the catch-all patterns:

```python
path("page/", include("apps.cms.urls")),
```

### 5. Admin (`apps/cms/admin.py`)

```python
from django.contrib import admin
from django import forms
from apps.cms.models import Page

class CardInlineFormset(forms.BaseInlineFormSet):
    """Custom formset for card editing."""
    pass

@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ["title", "slug", "status", "hero_enabled", "updated_at"]
    list_filter = ["status", "hero_enabled", "require_login", "require_team_member"]
    search_fields = ["title", "slug", "content"]
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ["created_at", "updated_at", "created_by"]

    fieldsets = [
        (None, {
            "fields": ["title", "slug", "status"]
        }),
        ("Content", {
            "fields": ["content"],
            "description": "Use Markdown formatting"
        }),
        ("Hero Section", {
            "fields": ["hero_enabled", "hero_image", "hero_title", "hero_subtitle"],
            "classes": ["collapse"]
        }),
        ("Card Sections", {
            "fields": ["cards_above", "cards_below"],
            "classes": ["collapse"],
            "description": "JSON array of card objects"
        }),
        ("SEO", {
            "fields": ["meta_description"],
            "classes": ["collapse"]
        }),
        ("Access Control", {
            "fields": ["require_login", "require_team_member"]
        }),
        ("Metadata", {
            "fields": ["created_at", "updated_at", "created_by"],
            "classes": ["collapse"]
        }),
    ]

    def save_model(self, request, obj, form, change):
        if not change:  # New object
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
```

### 6. Page Template (`templates/cms/page_detail.html`)

```html
{% extends "base.html" %}
{% load markdown_extras %}

{% block title %}{{ page.title }} - {{ config.TEAM_NAME }}{% endblock %}

{% block content %}
<!-- Hero Section (optional) -->
{% if page.hero_enabled %}
{% include "cms/partials/_hero.html" with hero=page %}
{% endif %}

<!-- Cards Above (optional) -->
{% if page.cards_above %}
{% include "cms/partials/_cards.html" with cards=page.cards_above %}
{% endif %}

<!-- Main Content -->
<div class="max-w-4xl mx-auto py-8 px-4">
    {% if not page.hero_enabled %}
    <h1 class="text-4xl font-bold mb-8">{{ page.title }}</h1>
    {% endif %}

    <div class="prose prose-lg max-w-none">
        {{ content_html|safe }}
    </div>
</div>

<!-- Cards Below (optional) -->
{% if page.cards_below %}
{% include "cms/partials/_cards.html" with cards=page.cards_below %}
{% endif %}
{% endblock %}
```

### 7. Hero Partial (`templates/cms/partials/_hero.html`)

```html
<div class="hero min-h-[400px] {% if hero.hero_image %}bg-cover bg-center{% else %}bg-base-200{% endif %}"
     {% if hero.hero_image %}style="background-image: linear-gradient(rgba(0,0,0,0.6), rgba(0,0,0,0.6)), url('{{ hero.hero_image.url }}')"{% endif %}>
    <div class="hero-content text-center {% if hero.hero_image %}text-neutral-content{% endif %}">
        <div class="max-w-2xl">
            <h1 class="text-5xl font-bold mb-4">{{ hero.hero_title|default:hero.title }}</h1>
            {% if hero.hero_subtitle %}
            <p class="text-xl">{{ hero.hero_subtitle }}</p>
            {% endif %}
        </div>
    </div>
</div>
```

### 8. Cards Partial (`templates/cms/partials/_cards.html`)

```html
<div class="bg-base-200 py-12">
    <div class="max-w-6xl mx-auto px-4">
        <div class="grid grid-cols-1 md:grid-cols-{{ cards|length|default:3 }} gap-6">
            {% for card in cards %}
            <div class="card bg-base-100 shadow-xl">
                <div class="card-body items-center text-center">
                    {% if card.icon %}
                    <span class="text-4xl mb-2">{{ card.icon }}</span>
                    {% endif %}
                    <h3 class="card-title">{{ card.title }}</h3>
                    <p>{{ card.description }}</p>
                    {% if card.link_url %}
                    <div class="card-actions mt-4">
                        <a href="{{ card.link_url }}" class="btn btn-primary btn-sm">
                            {{ card.link_text|default:"Learn More" }}
                        </a>
                    </div>
                    {% endif %}
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
</div>
```

---

## Dependencies

Add to `pyproject.toml`:

```toml
markdown = "^3.5"
```

Already available: Pillow (for ImageField)

---

## Migration Steps

1. Create the app: `mkdir -p apps/cms`
2. Create all model files
3. Add to INSTALLED_APPS
4. Run `python manage.py makemigrations cms`
5. Run `python manage.py migrate`
6. Add URL configuration
7. Create templates

---

## Admin UI Enhancements (Future)

### Phase 2: Custom Card Editor

Instead of raw JSON editing, create a custom admin interface:

1. **Inline Card Editor** - JavaScript-based card builder
2. **Card Preview** - Live preview of card grid
3. **Drag & Drop** - Reorder cards
4. **Image Upload for Cards** - Optional card images

### Phase 3: Page Builder

1. **WYSIWYG Markdown Editor** - Use EasyMDE or similar
2. **Section Builder** - Add/remove/reorder sections
3. **Template Selection** - Choose from predefined layouts

---

## Sidebar Integration

Add to `theme/templates/sidebar.html` in Admin section:

```html
{% if user.is_app_admin or user.is_superuser %}
<li>
    <a href="{% url 'admin:cms_page_changelist' %}" class="{% if 'cms/page' in request.path %}active{% endif %}">
        <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z" />
        </svg>
        CMS Pages
    </a>
</li>
{% endif %}
```

---

## Permission Model

| Permission | Who Can |
|------------|---------|
| Create/Edit Pages | `app_admin` or superuser |
| View Draft Pages | `app_admin` or superuser |
| View Published Pages | Based on page settings (public, login required, team member required) |

---

## Testing Checklist

- [ ] Create page with all fields
- [ ] Verify slug uniqueness
- [ ] Test markdown rendering
- [ ] Test hero with/without image
- [ ] Test cards above content
- [ ] Test cards below content
- [ ] Test draft vs published visibility
- [ ] Test require_login permission
- [ ] Test require_team_member permission
- [ ] Test admin interface
- [ ] Test SEO meta description

---

## Future Enhancements

1. **Page Hierarchy** - Parent/child pages for navigation
2. **Scheduled Publishing** - Publish/unpublish at specific times
3. **Version History** - Track content changes
4. **Page Templates** - Predefined layouts (landing, documentation, etc.)
5. **Navigation Menus** - Dynamic menu builder
6. **Media Library** - Centralized image management
7. **Search** - Full-text search across pages
