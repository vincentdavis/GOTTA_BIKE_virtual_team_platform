"""Views for GOTTA_BIKE_virtual_team_platform project."""

import logfire
import markdown
from constance import config
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.cms.models import Page

# AI crawlers to block when ROBOTS_DISALLOW_AI is enabled
AI_CRAWLERS = [
    "GPTBot",  # OpenAI
    "ChatGPT-User",  # OpenAI
    "CCBot",  # Common Crawl (used for AI training)
    "anthropic-ai",  # Anthropic
    "Claude-Web",  # Anthropic
    "Google-Extended",  # Google AI training
    "Bytespider",  # ByteDance/TikTok
    "Amazonbot",  # Amazon
    "FacebookBot",  # Meta
    "Meta-ExternalAgent",  # Meta AI
    "PerplexityBot",  # Perplexity AI
    "Cohere-ai",  # Cohere
    "Applebot-Extended",  # Apple AI
    "Diffbot",  # Diffbot
    "ImagesiftBot",  # AI image training
    "Omgilibot",  # Webz.io AI
]


@require_GET
def home(request):
    """Render the home page.

    Uses different CMS pages based on authentication status:
    - Authenticated users: HOME_PAGE_SLUG_AUTHENTICATED (falls back to HOME_PAGE_SLUG)
    - Non-authenticated users: HOME_PAGE_SLUG

    If no matching published CMS page exists, falls back to the default index.html template.

    Args:
        request: The HTTP request.

    Returns:
        Rendered home page template.

    """
    # Determine which page slug to use based on authentication
    if request.user.is_authenticated and config.HOME_PAGE_SLUG_AUTHENTICATED:
        slug = config.HOME_PAGE_SLUG_AUTHENTICATED
    else:
        slug = config.HOME_PAGE_SLUG

    if slug:
        try:
            page = Page.objects.get(slug=slug, status=Page.Status.PUBLISHED)
        except Page.DoesNotExist:
            logfire.warning("HOME_PAGE_SLUG configured but page not found or not published", slug=slug)
            return render(request, "index.html")

        content_html = ""
        if page.content:
            content_html = markdown.markdown(
                page.content,
                extensions=["extra", "codehilite", "toc", "nl2br", "tables"],
            )

        hero_subtitle_html = ""
        if page.hero_subtitle:
            hero_subtitle_html = markdown.markdown(
                page.hero_subtitle,
                extensions=["nl2br"],
            )

        logfire.info(
            "Home page served from CMS",
            slug=slug,
            page_id=page.id,
            is_authenticated=request.user.is_authenticated,
        )
        context = {
            "page": page,
            "content_html": content_html,
            "hero_subtitle_html": hero_subtitle_html,
        }
        return render(request, "cms/page_detail.html", context)

    return render(request, "index.html")


@require_GET
def about(request):
    """Render the about page.

    Args:
        request: The HTTP request.

    Returns:
        Rendered about page template.

    """
    return render(request, "about.html")


@require_GET
def robots_txt(request):
    """Generate dynamic robots.txt based on Constance settings.

    Args:
        request: The HTTP request.

    Returns:
        Plain text robots.txt response.

    """
    lines = []

    if config.ROBOTS_DISALLOW_ALL:
        # Block all crawlers
        lines.extend([
            "User-agent: *",
            "Disallow: /",
        ])
    elif config.ROBOTS_DISALLOW_AI:
        # Block AI crawlers only
        for crawler in AI_CRAWLERS:
            lines.extend([
                f"User-agent: {crawler}",
                "Disallow: /",
                "",
            ])
        # Allow other crawlers
        lines.extend([
            "User-agent: *",
            "Allow: /",
        ])
    else:
        # Allow all crawlers (default)
        lines.extend([
            "User-agent: *",
            "Allow: /",
        ])

    content = "\n".join(lines)
    return HttpResponse(content, content_type="text/plain")
