"""The project's copy of allauth's layout declares the page language (WCAG 3.1.1).

``templates/allauth/layouts/base.html`` replaces allauth's own layout, which renders ``<html>``
with no ``lang``. Every allauth page the site does not restyle uses it, so a screen reader
would otherwise have to guess the language. The copy is re-diffed on an allauth upgrade; this
test fails if that drops the attribute again.
"""

import re

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

LAYOUT = "allauth/layouts/base.html"
HTML_LANG = re.compile(r'<html\s+lang="en(-[a-z]+)?"', re.IGNORECASE)


def _template_names(response):
    return {template.name for template in response.templates}


def test_the_layout_declares_the_page_language():
    assert HTML_LANG.search(render_to_string(LAYOUT))


@pytest.mark.django_db
def test_the_account_connections_page_declares_its_language(client, team_member):
    client.force_login(team_member)

    response = client.get(reverse("socialaccount_connections"))

    assert response.status_code == 200
    assert LAYOUT in _template_names(response)
    assert HTML_LANG.search(response.content.decode())


@pytest.mark.django_db
def test_the_signup_closed_page_declares_its_language(client):
    response = client.get(reverse("account_signup"))

    assert response.status_code == 200
    assert LAYOUT in _template_names(response)
    assert HTML_LANG.search(response.content.decode())
