"""Views for GOTTA_BIKE_virtual_team_platform project."""

import logfire
import markdown
from allauth.account.views import login as allauth_login
from constance import config
from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
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


def block_social_signup(request):
    """Block the allauth social signup page and redirect to login.

    This prevents users from creating disconnected accounts via the
    allauth 3rd-party signup form. All account creation must go through
    the Discord OAuth flow.

    Args:
        request: The HTTP request.

    Returns:
        Redirect to login page with error message.

    """
    logfire.warning(
        "Blocked social signup page access",
        user=str(request.user),
        method=request.method,
    )
    messages.error(
        request,
        "Account signup is only available through Discord. "
        "Please click 'Sign in with Discord' to log in or create an account.",
    )
    return redirect("account_login")


def closed_account_route(request, *args, route: str = "", **kwargs):
    """404 for the allauth routes that would let somebody in without Discord.

    With ``ACCOUNT_LOGIN_METHODS = {"email"}`` and no password field, allauth treats an
    email address as enough to start a login by emailed code, and its password reset and
    password set pages would give a password to an account that was only ever meant to sign
    in through Discord. Any of those skips the block list and the guild check in
    ``DiscordSocialAccountAdapter.pre_social_login``. Mounted ahead of the allauth include in
    ``gotta_bike_platform/urls.py``, the same way ``block_social_signup`` shadows the social
    signup page. ``SOCIALACCOUNT_ONLY`` would remove these routes itself, but allauth refuses
    it alongside ``allauth.mfa``.

    Args:
        request: The HTTP request.
        *args: Positional URL arguments (ignored).
        route: Label of the closed route family, for the log.
        **kwargs: Keyword URL arguments (ignored).

    Raises:
        Http404: Always.

    """
    # The label, not request.path: a password-reset path carries the reset key.
    logfire.info("Refused a closed allauth route", route=route, method=request.method)
    raise Http404


def discord_only_login(request, *args, **kwargs):
    """Serve allauth's login page for GET only: the page offers Discord and nothing else.

    ``templates/account/login.html`` has no form; its only control links to the Discord
    provider (``/accounts/discord/login/``, a different route). A POST here can only be a
    hand-made one, and allauth would read its email address as a request to email a login
    code -- a way in that never touches Discord. It is sent back to the page as a GET.

    Args:
        request: The HTTP request.
        *args: Positional URL arguments, passed through.
        **kwargs: Keyword URL arguments, passed through.

    Returns:
        allauth's login response for GET/HEAD, otherwise a redirect to the same page.

    """
    if request.method not in {"GET", "HEAD"}:
        logfire.warning("Refused a non-GET request to the login page", method=request.method)
        return redirect(request.get_full_path())
    return allauth_login(request, *args, **kwargs)


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


@require_GET
def healthz(request):
    """Return service health plus deploy metadata for programmatic version checks.

    Public, unauthenticated, dependency-free (no DB hit) so it is safe as a
    liveness/version probe.

    Args:
        request: The HTTP request.

    Returns:
        JSON response with status, version (short commit SHA), and deploy time.

    """
    from gotta_bike_platform.version import DEPLOY_TIME, DEPLOY_VERSION

    return JsonResponse({
        "status": "ok",
        "version": DEPLOY_VERSION or None,
        "deployed_at": DEPLOY_TIME.isoformat(),
    })
