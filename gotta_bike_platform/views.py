"""Views for GOTTA_BIKE_virtual_team_platform project."""

from constance import config
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

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

    Args:
        request: The HTTP request.

    Returns:
        Rendered home page template.

    """
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
