"""Models for CMS app."""

from typing import ClassVar

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class Page(models.Model):
    """Dynamic CMS page with markdown content and optional hero/cards sections.

    Pages can be configured with:
    - Markdown content (main body)
    - Optional hero section with image, title, and subtitle
    - Optional card sections above/below content
    - Access control (login required, team member required)
    - Navigation visibility settings

    Attributes:
        slug: URL slug for the page (unique).
        title: Display title for the page.
        content: Markdown content for the main body.
        hero_enabled: Whether to show the hero section.
        hero_image: Optional background image for hero.
        hero_title: Title text for hero section.
        hero_subtitle: Subtitle text for hero section.
        cards_above: JSON array of card objects above content.
        cards_below: JSON array of card objects below content.
        show_in_nav: Whether to show in sidebar navigation.
        nav_title: Override title for navigation (optional).
        nav_order: Sort order in navigation (lower = higher).
        status: Draft or published status.
        require_login: Whether login is required to view.
        require_team_member: Whether team_member permission is required.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
        created_by: User who created the page.

    """

    class Status(models.TextChoices):
        """Page publication status choices."""

        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    # Identity
    slug = models.SlugField(
        max_length=100,
        unique=True,
        help_text="URL slug for the page (e.g., 'about-us' creates /page/about-us/)",
    )
    title = models.CharField(
        max_length=200,
        help_text="Page title displayed in header and browser tab",
    )

    # Content
    content = models.TextField(
        blank=True,
        help_text="Main content in Markdown format",
    )

    # Hero section (optional)
    hero_enabled = models.BooleanField(
        default=False,
        help_text="Show hero section at the top of the page",
    )
    hero_image = models.ImageField(
        upload_to="cms/hero/%Y/%m/",
        blank=True,
        null=True,
        help_text="Background image for the hero section",
    )
    hero_title = models.CharField(
        max_length=200,
        blank=True,
        help_text="Hero section title (defaults to page title if empty)",
    )
    hero_subtitle = models.TextField(
        blank=True,
        help_text="Hero section subtitle (supports Markdown)",
    )

    # Cards sections (JSON arrays)
    cards_above = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'Cards above content. Format: [{"icon": "...", "title": "...", '
            '"description": "...", "link_url": "...", "link_text": "..."}]'
        ),
    )
    cards_below = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'Cards below content. Format: [{"icon": "...", "title": "...", '
            '"description": "...", "link_url": "...", "link_text": "..."}]'
        ),
    )

    # Navigation settings
    show_in_nav = models.BooleanField(
        default=False,
        help_text="Show this page in the sidebar navigation",
    )
    nav_title = models.CharField(
        max_length=50,
        blank=True,
        help_text="Override title for navigation (uses page title if empty)",
    )
    nav_order = models.IntegerField(
        default=0,
        help_text="Sort order in navigation (lower numbers appear first)",
    )

    # Access control
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT,
        help_text="Draft pages are only visible to admins",
    )
    require_login = models.BooleanField(
        default=False,
        help_text="Require user to be logged in to view this page",
    )
    require_team_member = models.BooleanField(
        default=False,
        help_text="Require team_member permission to view this page",
    )

    # Timestamps and tracking
    created_at = models.DateTimeField(
        default=timezone.now,
        help_text="When the page was created",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When the page was last updated",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_pages",
        help_text="User who created this page",
    )

    class Meta:
        """Meta options for Page model."""

        verbose_name = "Page"
        verbose_name_plural = "Pages"
        ordering: ClassVar[list[str]] = ["nav_order", "title"]

    def __str__(self) -> str:
        """Return string representation of page.

        Returns:
            The page title with status indicator.

        """
        status_indicator = "" if self.status == self.Status.PUBLISHED else " [Draft]"
        return f"{self.title}{status_indicator}"

    def get_absolute_url(self) -> str:
        """Return the absolute URL for this page.

        Returns:
            URL path to the page detail view.

        """
        return reverse("cms:page_detail", kwargs={"slug": self.slug})

    @property
    def is_published(self) -> bool:
        """Check if page is published.

        Returns:
            True if status is PUBLISHED.

        """
        return self.status == self.Status.PUBLISHED

    @property
    def is_draft(self) -> bool:
        """Check if page is a draft.

        Returns:
            True if status is DRAFT.

        """
        return self.status == self.Status.DRAFT

    @property
    def display_nav_title(self) -> str:
        """Return the title to display in navigation.

        Returns:
            nav_title if set, otherwise title.

        """
        return self.nav_title or self.title

    @property
    def display_hero_title(self) -> str:
        """Return the title to display in the hero section.

        Returns:
            hero_title if set, otherwise title.

        """
        return self.hero_title or self.title
