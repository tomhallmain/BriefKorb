"""Django settings module used only for tests/django/ view tests.

Inherits everything from the real django_app.settings, then overrides the
two pieces that would otherwise touch real, shared state:

- DATABASES: the real settings point at a file-based sqlite3 db under
  BASE_DIR (app/db.sqlite3). None of the views under test touch the ORM
  (no View here queries a model), but pointing this at ':memory:' means
  that stays true even if that changes, without ever creating a real file.
- SESSION_ENGINE: default Django sessions are DB-backed, which would
  require running migrations to create the django_session table before any
  test could use request.session -- and nearly every view here does
  (home/oauth/calendar/messages all read or write request.session).
  Cache-backed sessions (against an in-process locmem cache) avoid that
  without touching a database, and -- unlike signed_cookies sessions, whose
  save() is a no-op with nothing for a session_key to reference -- still
  support the standard `client.session; session[...] = x; session.save()`
  pattern tests use to pre-seed session state before a request.
"""

from django_app.settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}

SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
