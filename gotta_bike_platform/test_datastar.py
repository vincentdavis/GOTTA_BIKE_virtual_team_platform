"""Smoke tests for the datastar-py integration.

Guards that the SDK is installed and wired so views can return Datastar SSE responses,
and that the datastar client script is loaded site-wide via base.html.
"""


def test_datastar_django_imports():
    from datastar_py.django import (  # noqa: F401
        DatastarResponse,
        ServerSentEventGenerator,
        read_signals,
    )


def test_datastar_response_emits_sse():
    from datastar_py.django import DatastarResponse, ServerSentEventGenerator

    def gen():
        yield ServerSentEventGenerator.patch_elements('<span id="x">hi</span>')
        yield ServerSentEventGenerator.patch_signals({"count": 1})

    response = DatastarResponse(gen())
    assert response.headers["Content-Type"].startswith("text/event-stream")
    body = b"".join(response.streaming_content).decode()
    assert "event: datastar-patch-elements" in body
    assert "event: datastar-patch-signals" in body
    assert '<span id="x">hi</span>' in body


def test_datastar_script_loaded_in_base(client, db, django_user_model):
    # An authenticated page renders base.html, which should include the datastar module script.
    user = django_user_model.objects.create_user(username="ds_tester", password="x")  # noqa: S106
    client.force_login(user)
    response = client.get("/")
    assert response.status_code == 200
    assert b"starfederation/datastar@v1.0.2" in response.content
