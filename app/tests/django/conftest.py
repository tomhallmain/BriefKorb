"""
conftest for tests/django/ (Django view tests -- Tier 3, not yet written).

Mirrors the module-level env var bootstrap from the root conftest.py.
Normally pytest fully loads every ancestor conftest.py (root down to this
directory) before collecting tests here, so the root conftest's bootstrap
should already have run -- this is defensive redundancy for invocation modes
that bypass that ordering (e.g. running pytest directly against this
subdirectory with a rootdir override). `setdefault` makes it a no-op
whenever the root conftest already set these.

Only the module-level env var bootstrap is mirrored -- the autouse
isolated_app_state fixture in the root conftest.py is inherited normally by
every test under this directory and does not need to be duplicated here.
Django-specific bootstrap (DJANGO_SETTINGS_MODULE, django.setup()) belongs
here too once Tier 3 work starts.
"""

import os

if "BRIEFKORB_CACHE_DIR" not in os.environ:
    import tempfile
    import atexit
    import shutil

    _tmp = tempfile.mkdtemp(prefix="briefkorb_django_tests_")
    os.environ["BRIEFKORB_CACHE_DIR"] = os.path.join(_tmp, "cache")
    os.environ["BRIEFKORB_LOG_DIR"] = os.path.join(_tmp, "logs")
    os.environ["BRIEFKORB_CONFIG_PATH"] = os.path.join(_tmp, "config.yaml")
    os.environ["BRIEFKORB_TOKEN_STORAGE_PATH"] = os.path.join(_tmp, "tokens")
    os.makedirs(os.environ["BRIEFKORB_CACHE_DIR"], exist_ok=True)
    os.makedirs(os.environ["BRIEFKORB_LOG_DIR"], exist_ok=True)
    os.makedirs(os.environ["BRIEFKORB_TOKEN_STORAGE_PATH"], exist_ok=True)
    atexit.register(shutil.rmtree, _tmp, True)


# ---------------------------------------------------------------------------
# Django bootstrap.
#
# django_test_settings.py (this directory) is a thin wrapper around the real
# django_app.settings -- see its docstring for why DATABASES/SESSION_ENGINE
# are overridden. django.setup() must run before any test module in this
# directory imports django_app views (which import Django internals like
# django.shortcuts.render at module scope), so it happens here at conftest
# import time rather than in a fixture.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_test_settings")

import django  # noqa: E402
django.setup()

import pytest  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment, teardown_test_environment  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _django_test_environment():
    setup_test_environment()
    yield
    teardown_test_environment()


@pytest.fixture
def client() -> Client:
    return Client()
