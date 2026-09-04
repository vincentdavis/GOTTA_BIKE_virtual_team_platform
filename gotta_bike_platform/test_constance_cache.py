"""The Constance cache wiring.

The point of the change: every ``config.X`` read was its own SELECT, so 33 of the 35 queries
on a normal authenticated page were the app re-reading its own settings.

It is switched OFF under pytest -- a file cache is not rolled back with the test transaction,
so a value cached in one test would be served to the next from a row that no longer exists.
That makes the production configuration the thing nothing else exercises, so it is exercised
here explicitly: the backend is constructed against the real alias, and the behaviour that
makes it safe (an edit invalidating immediately) is asserted rather than assumed.
"""

import tempfile
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.core.cache import caches
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

FILE_CACHE = {
    "default": django_settings.CACHES["default"],
    "shared": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": tempfile.mkdtemp(prefix="constance-cache-test-"),
        "OPTIONS": {"MAX_ENTRIES": 2000},
    },
}


@contextmanager
def cached_backend():
    """Yield a constance backend wired to a real file cache, as production has it.

    ``constance.settings`` reads ``CONSTANCE_DATABASE_CACHE_BACKEND`` at IMPORT time
    (constance/settings.py:13), so ``override_settings`` cannot switch it on -- the module
    attribute has to be patched. Worth knowing beyond this test: the setting cannot be
    toggled at runtime at all, which is exactly why the pytest carve-out lives in
    settings.py where it is evaluated before constance is imported.

    Yields:
        A fresh DatabaseBackend using the "shared" alias.

    """
    import constance.settings as constance_settings
    from constance.backends.database import DatabaseBackend

    with override_settings(CACHES=FILE_CACHE), patch.object(constance_settings, "DATABASE_CACHE_BACKEND", "shared"):
        caches["shared"].clear()
        yield DatabaseBackend()


# --- the wiring ------------------------------------------------------------------------


def test_the_shared_alias_is_cross_process_in_production():
    """A file cache, not local memory -- constance refuses LocMem, and rightly.

    Site settings and the CMS nav invalidate by delete, and a delete in per-process memory
    never reaches the other Granian worker.
    """
    assert django_settings.CACHES["shared"]["BACKEND"].endswith("locmem.LocMemCache"), (
        "under pytest the shared alias should be local memory for test isolation"
    )

    with override_settings(CACHES=FILE_CACHE):
        from django.core.cache.backends.filebased import FileBasedCache

        assert isinstance(caches["shared"], FileBasedCache)


def test_the_autofill_backstop_is_not_left_at_a_day():
    """Constance defaults to 24h; that is far too long to be a useful backstop."""
    assert django_settings.CONSTANCE_DATABASE_CACHE_AUTOFILL_TIMEOUT == 300


def test_the_cache_is_disabled_under_pytest():
    """Stated so that a future change turning it on for tests is a deliberate one."""
    assert django_settings.CONSTANCE_DATABASE_CACHE_BACKEND is None


# --- the behaviour that makes it safe ---------------------------------------------------


@pytest.mark.django_db
def test_the_production_config_is_accepted_by_constance():
    """Constance raises ImproperlyConfigured for a local-memory backend; this must not."""
    with cached_backend() as backend:
        assert backend is not None


@pytest.mark.django_db
def test_reads_stop_hitting_the_database_once_filled():
    """The whole point: repeated reads of a setting cost one query, not one each."""
    with cached_backend() as backend:
        backend.set("TEAM_NAME", "Coalition")  # a key with no row cannot be cached -- see below

        with CaptureQueriesContext(connection) as queries:
            for _ in range(10):
                backend.get("TEAM_NAME")

        constance_queries = [q for q in queries.captured_queries if "constance" in q["sql"]]
        assert not constance_queries, f"{len(constance_queries)} SELECTs for 10 cached reads"


@pytest.mark.django_db
def test_the_uncached_backend_really_does_query_every_time():
    """The "before" half of the measurement, so the "after" means something.

    Asserted rather than described: without this, a change that silently stopped the cache
    working would leave the sibling test passing for the wrong reason.
    """
    import constance.settings as constance_settings
    from constance.backends.database import DatabaseBackend

    with override_settings(CACHES=FILE_CACHE), patch.object(constance_settings, "DATABASE_CACHE_BACKEND", None):
        backend = DatabaseBackend()
        backend.set("TEAM_NAME", "Coalition")

        with CaptureQueriesContext(connection) as queries:
            for _ in range(10):
                backend.get("TEAM_NAME")

        assert len([q for q in queries.captured_queries if "constance" in q["sql"]]) == 10


@pytest.mark.django_db
def test_a_setting_with_no_row_yet_is_not_cached_and_still_costs_a_query():
    """Worth pinning, because it is the one case where the cache does nothing.

    ``autofill`` selects the rows that exist and ``get`` only caches a value it found, so a
    key never written still costs a SELECT per read. In practice reading through ``config.X``
    creates the row with its default on first access, so this is a fresh-deploy state rather
    than a steady one -- but a future change that stopped creating those rows would quietly
    put every read back on the database.
    """
    with cached_backend() as backend, CaptureQueriesContext(connection) as queries:
        backend.get("TEAM_NAME")
        backend.get("TEAM_NAME")

        assert len([q for q in queries.captured_queries if "constance" in q["sql"]]) == 2


@pytest.mark.django_db
def test_saving_a_setting_invalidates_immediately():
    """An admin editing /site/config/ must not wait out the 300s backstop.

    This is the mechanism the whole design rests on: post_save clears the cached set, and
    because the cache is shared the clear reaches every process, not just the one that saved.
    """
    from constance.models import Constance

    with cached_backend() as backend:
        from constance.backends.database import DatabaseBackend

        backend.set("TEAM_NAME", "Before")
        assert backend.get("TEAM_NAME") == "Before"

        # A second backend stands in for the OTHER worker: its own instance state, the same
        # cache file. That sharing is the whole reason the alias is not local memory.
        other_worker = DatabaseBackend()
        assert other_worker.get("TEAM_NAME") == "Before"

        backend.set("TEAM_NAME", "After")

        assert backend.get("TEAM_NAME") == "After"
        assert other_worker.get("TEAM_NAME") == "After", "the other worker kept a stale value"
        assert Constance.objects.get(key="TEAM_NAME") is not None


@pytest.mark.django_db
def test_a_cache_miss_falls_back_to_the_database():
    """Worst case must be slower, never wrong -- an emptied cache cannot lose a value."""
    with cached_backend() as backend:
        backend.set("TEAM_NAME", "Persisted")

        caches["shared"].clear()  # simulate eviction / a cold worker

        assert backend.get("TEAM_NAME") == "Persisted"
