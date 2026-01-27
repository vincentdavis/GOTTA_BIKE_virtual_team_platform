"""Views for CMS app."""

import logfire
import markdown
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.cms.forms import PageForm
from apps.cms.models import Page


def page_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Display a CMS page.

    Args:
        request: The HTTP request.
        slug: The page slug from the URL.

    Returns:
        Rendered page template.

    Raises:
        Http404: If page not found or user lacks permission.

    """
    try:
        page = Page.objects.get(slug=slug)
    except Page.DoesNotExist:
        logfire.warning("CMS page not found", slug=slug)
        raise Http404("Page not found") from None

    # Check if page is published (drafts only visible to admins)
    if page.is_draft:
        if not request.user.is_authenticated:
            logfire.debug("Draft page access denied - not authenticated", slug=slug)
            raise Http404("Page not found")
        if not (request.user.is_superuser or getattr(request.user, "is_app_admin", False)):
            logfire.debug(
                "Draft page access denied - not admin",
                slug=slug,
                user_id=request.user.id,
            )
            raise Http404("Page not found")

    # Check login requirement
    if page.require_login and not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        logfire.debug("CMS page requires login", slug=slug)
        return redirect_to_login(request.get_full_path())

    # Check team member requirement
    if page.require_team_member:
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            logfire.debug("CMS page requires team member - not authenticated", slug=slug)
            return redirect_to_login(request.get_full_path())
        if not getattr(request.user, "has_permission", lambda x: False)("team_member"):
            logfire.warning(
                "CMS page access denied - not team member",
                slug=slug,
                user_id=request.user.id,
            )
            raise Http404("Page not found")

    # Render markdown content
    content_html = ""
    if page.content:
        content_html = markdown.markdown(
            page.content,
            extensions=[
                "extra",  # Tables, fenced code, footnotes, etc.
                "codehilite",  # Syntax highlighting
                "toc",  # Table of contents
                "nl2br",  # Newlines to <br>
                "tables",  # Table support
            ],
        )

    # Render hero subtitle if present
    hero_subtitle_html = ""
    if page.hero_subtitle:
        hero_subtitle_html = markdown.markdown(
            page.hero_subtitle,
            extensions=["nl2br"],
        )

    logfire.info(
        "CMS page viewed",
        slug=slug,
        page_id=page.id,
        user_id=request.user.id if request.user.is_authenticated else None,
    )

    context = {
        "page": page,
        "content_html": content_html,
        "hero_subtitle_html": hero_subtitle_html,
    }
    return render(request, "cms/page_detail.html", context)


def _check_cms_admin(user) -> bool:
    """Check if user has CMS admin permissions.

    Args:
        user: The user to check.

    Returns:
        True if user can manage CMS pages.

    """
    return user.is_superuser or getattr(user, "is_app_admin", False) or getattr(user, "is_pages_admin", False)


@login_required
def page_list(request: HttpRequest) -> HttpResponse:
    """List all CMS pages for management.

    Args:
        request: The HTTP request.

    Returns:
        Rendered page list template.

    Raises:
        PermissionDenied: If user lacks admin permissions.

    """
    if not _check_cms_admin(request.user):
        logfire.warning(
            "CMS page list access denied",
            user_id=request.user.id,
        )
        raise PermissionDenied("You don't have permission to manage pages.")

    # Get filter parameters
    search_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "")
    nav_filter = request.GET.get("nav", "")

    # Build queryset
    pages = Page.objects.all().order_by("nav_order", "title")

    if search_query:
        pages = pages.filter(title__icontains=search_query) | pages.filter(slug__icontains=search_query)

    if status_filter:
        pages = pages.filter(status=status_filter)

    if nav_filter == "yes":
        pages = pages.filter(show_in_nav=True)
    elif nav_filter == "no":
        pages = pages.filter(show_in_nav=False)

    logfire.info(
        "CMS page list viewed",
        user_id=request.user.id,
        page_count=pages.count(),
    )

    context = {
        "pages": pages,
        "search_query": search_query,
        "status_filter": status_filter,
        "nav_filter": nav_filter,
        "status_choices": Page.Status.choices,
    }
    return render(request, "cms/page_list.html", context)


@login_required
def page_create(request: HttpRequest) -> HttpResponse:
    """Create a new CMS page.

    Args:
        request: The HTTP request.

    Returns:
        Rendered form or redirect on success.

    Raises:
        PermissionDenied: If user lacks admin permissions.

    """
    if not _check_cms_admin(request.user):
        logfire.warning(
            "CMS page create access denied",
            user_id=request.user.id,
        )
        raise PermissionDenied("You don't have permission to create pages.")

    if request.method == "POST":
        form = PageForm(request.POST, request.FILES)
        if form.is_valid():
            page = form.save(commit=False)
            page.created_by = request.user
            page.save()
            logfire.info(
                "CMS page created",
                page_id=page.id,
                slug=page.slug,
                user_id=request.user.id,
            )
            messages.success(request, f"Page '{page.title}' created successfully.")
            return redirect("cms:page_list")
        logfire.warning(
            "CMS page create form invalid",
            user_id=request.user.id,
            errors=form.errors.as_json(),
        )
    else:
        form = PageForm()

    context = {
        "form": form,
        "is_edit": False,
    }
    return render(request, "cms/page_form.html", context)


@login_required
def page_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Edit an existing CMS page.

    Args:
        request: The HTTP request.
        pk: The page primary key.

    Returns:
        Rendered form or redirect on success.

    Raises:
        PermissionDenied: If user lacks admin permissions.

    """
    if not _check_cms_admin(request.user):
        logfire.warning(
            "CMS page edit access denied",
            user_id=request.user.id,
            page_id=pk,
        )
        raise PermissionDenied("You don't have permission to edit pages.")

    page = get_object_or_404(Page, pk=pk)

    if request.method == "POST":
        form = PageForm(request.POST, request.FILES, instance=page)
        if form.is_valid():
            form.save()
            logfire.info(
                "CMS page updated",
                page_id=page.id,
                slug=page.slug,
                user_id=request.user.id,
            )
            messages.success(request, f"Page '{page.title}' updated successfully.")
            return redirect("cms:page_list")
        logfire.warning(
            "CMS page edit form invalid",
            user_id=request.user.id,
            page_id=pk,
            errors=form.errors.as_json(),
        )
    else:
        form = PageForm(instance=page)

    context = {
        "form": form,
        "page": page,
        "is_edit": True,
    }
    return render(request, "cms/page_form.html", context)


@login_required
def page_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete a CMS page.

    Args:
        request: The HTTP request.
        pk: The page primary key.

    Returns:
        Redirect on success or rendered confirmation.

    Raises:
        PermissionDenied: If user lacks admin permissions.

    """
    if not _check_cms_admin(request.user):
        logfire.warning(
            "CMS page delete access denied",
            user_id=request.user.id,
            page_id=pk,
        )
        raise PermissionDenied("You don't have permission to delete pages.")

    page = get_object_or_404(Page, pk=pk)

    if request.method == "POST":
        title = page.title
        slug = page.slug
        page.delete()
        logfire.info(
            "CMS page deleted",
            page_title=title,
            slug=slug,
            user_id=request.user.id,
        )
        messages.success(request, f"Page '{title}' deleted successfully.")
        return redirect("cms:page_list")

    context = {
        "page": page,
    }
    return render(request, "cms/page_delete.html", context)
