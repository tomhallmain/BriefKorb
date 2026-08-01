import atexit
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


if "keyring" not in sys.modules:
    _store: dict[tuple[str, str], str] = {}
    keyring_stub = types.ModuleType("keyring")

    def _get_password(service_name: str, username: str):
        return _store.get((service_name, username))

    def _set_password(service_name: str, username: str, password: str):
        _store[(service_name, username)] = password

    def _delete_password(service_name: str, username: str):
        _store.pop((service_name, username), None)

    keyring_stub.get_password = _get_password
    keyring_stub.set_password = _set_password
    keyring_stub.delete_password = _delete_password
    sys.modules["keyring"] = keyring_stub


# ---------------------------------------------------------------------------
# Real-file isolation.
#
# Several email_server modules touch real, fixed filesystem locations as a
# side effect of being *imported*, not just when their classes are used:
#   - email_server/__init__.py, auth/__init__.py, auth/gmail.py,
#     auth/microsoft.py, providers/*/*.py, utils/app_info_cache.py all call
#     setup_logger(name) at module scope, which unconditionally creates a
#     real log directory (~/.local/share/email_server/logs or Windows
#     AppData) and starts writing to it on first import of each name.
#   - AppInfoCache's default (no storage_path) instance resolves to this
#     repo's real app_info_cache.enc/.bak* files under email_server/ and
#     tokens/ -- both of which contain real data in a normal checkout.
#   - EmailServerConfig.resolve_path() (used by every view/service that
#     loads config.yaml) defaults to the real app/email_server/config.yaml.
#   - TokenManager() with no explicit storage_path (the fallback used inside
#     MicrosoftOAuth, GmailOAuth, GmailProvider and MicrosoftGraphProvider
#     when no token_manager is passed in) defaults to a cwd-relative
#     "tokens" dir, which can resolve to this repo's real tokens/ directory.
#   - SenderCategorizationManager (and therefore SenderBlocklist /
#     BlockedSenderTracker, which persist through the same AppInfoCache-
#     backed self._cache) is fully covered by BRIEFKORB_CACHE_DIR above for
#     its *data* -- but its __init__ separately calls
#     load_sender_categorization_rules() for *rule definitions* unless a
#     test passes rules= explicitly, and that module has its own, unrelated
#     real-file bootstrap: sender_categorization_rules.py's
#     _bootstrap_local_rule_snapshots_if_allowed() writes
#     email_client/utils/data/sender_categorization_rules.{active,default}.json
#     from the bundled .enc the first time either is missing. In this
#     checkout those files already exist so the write is currently a no-op,
#     but that's incidental to current state, not guaranteed (a fresh clone
#     or a cleaned CI checkout would trigger a real write).
#     BRIEFKORB_SKIP_SENDER_RULES_FILE_BOOTSTRAP=1 disables that write path
#     unconditionally; it doesn't need to vary per test (nothing here is
#     tmp_path-derived) so setdefault at module load, same as the others, is
#     enough -- no autouse fixture re-application needed.
#
# BRIEFKORB_CACHE_DIR / BRIEFKORB_LOG_DIR / BRIEFKORB_CONFIG_PATH /
# BRIEFKORB_TOKEN_STORAGE_PATH must be set here, at conftest module load
# time, because pytest imports test modules (which transitively import the
# modules above) during collection -- *before* any fixture, including an
# autouse one, gets a chance to run. A fixture-only approach would arrive
# too late for the very first import of each module.
_bootstrap_dir = tempfile.mkdtemp(prefix="briefkorb_tests_")
os.environ.setdefault("BRIEFKORB_CACHE_DIR", os.path.join(_bootstrap_dir, "cache"))
os.environ.setdefault("BRIEFKORB_LOG_DIR", os.path.join(_bootstrap_dir, "logs"))
# Deliberately does not exist -- matches this repo's real (unconfigured)
# state, so config_path.exists() naturally reads False just like it does
# against the real path today.
os.environ.setdefault("BRIEFKORB_CONFIG_PATH", os.path.join(_bootstrap_dir, "config.yaml"))
os.environ.setdefault("BRIEFKORB_TOKEN_STORAGE_PATH", os.path.join(_bootstrap_dir, "tokens"))
os.environ.setdefault("BRIEFKORB_SKIP_SENDER_RULES_FILE_BOOTSTRAP", "1")
os.makedirs(os.environ["BRIEFKORB_CACHE_DIR"], exist_ok=True)
os.makedirs(os.environ["BRIEFKORB_LOG_DIR"], exist_ok=True)
os.makedirs(os.environ["BRIEFKORB_TOKEN_STORAGE_PATH"], exist_ok=True)
atexit.register(shutil.rmtree, _bootstrap_dir, True)


import pytest  # noqa: E402  (must follow the env var bootstrap above)


@pytest.fixture(autouse=True)
def isolated_app_state(tmp_path, monkeypatch):
    """Repoint the cache/log/config locations to a fresh per-test directory,
    and clear AppInfoCache's instance cache so no test can read stale state
    left by a previous one, or -- if BRIEFKORB_CACHE_DIR were ever unset --
    fall through to this repo's real production files.

    Tests that want a real config.yaml present should write one under
    tmp_path and monkeypatch BRIEFKORB_CONFIG_PATH to point at it; tests
    that construct AppInfoCache/SenderCategorizationManager/TokenManager
    directly should still pass an explicit tmp_path-derived storage_path
    rather than relying on this fixture alone.

    Everything this fixture creates lives under a dedicated "_isolation"
    subdirectory of tmp_path (not directly under tmp_path itself) so it can
    never collide with a test's own tmp_path-derived paths -- e.g. a test
    that independently does `tmp_path / "tokens"` for its own purposes.
    """
    isolation_root = tmp_path / "_isolation"
    cache_dir = isolation_root / "cache"
    log_dir = isolation_root / "logs"
    token_dir = isolation_root / "tokens"
    cache_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    token_dir.mkdir(parents=True)

    monkeypatch.setenv("BRIEFKORB_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("BRIEFKORB_LOG_DIR", str(log_dir))
    monkeypatch.setenv("BRIEFKORB_CONFIG_PATH", str(isolation_root / "config.yaml"))
    monkeypatch.setenv("BRIEFKORB_TOKEN_STORAGE_PATH", str(token_dir))

    import email_server.utils.app_info_cache as aic
    aic._cache_instances.clear()
    yield
    aic._cache_instances.clear()
