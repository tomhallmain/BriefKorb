"""conftest for tests/ui/ (PySide6/pytest-qt widget tests).

Mirrors the module-level env var bootstrap from the root conftest.py.
Normally pytest fully loads every ancestor conftest.py (root down to this
directory) before collecting tests here, so the root conftest's bootstrap
should already have run -- this is defensive redundancy for invocation modes
that bypass that ordering (e.g. running pytest directly against this
subdirectory with a rootdir override), matching the same pattern used by
tests/django/conftest.py. `setdefault` makes it a no-op whenever the root
conftest already set these.

Only the module-level env var bootstrap is mirrored -- the autouse
isolated_app_state fixture in the root conftest.py is inherited normally by
every test under this directory and does not need to be duplicated here.
"""

import os

if "BRIEFKORB_CACHE_DIR" not in os.environ:
    import tempfile
    import atexit
    import shutil

    _tmp = tempfile.mkdtemp(prefix="briefkorb_ui_tests_")
    os.environ["BRIEFKORB_CACHE_DIR"] = os.path.join(_tmp, "cache")
    os.environ["BRIEFKORB_LOG_DIR"] = os.path.join(_tmp, "logs")
    os.environ["BRIEFKORB_CONFIG_PATH"] = os.path.join(_tmp, "config.yaml")
    os.environ["BRIEFKORB_TOKEN_STORAGE_PATH"] = os.path.join(_tmp, "tokens")
    os.makedirs(os.environ["BRIEFKORB_CACHE_DIR"], exist_ok=True)
    os.makedirs(os.environ["BRIEFKORB_LOG_DIR"], exist_ok=True)
    os.makedirs(os.environ["BRIEFKORB_TOKEN_STORAGE_PATH"], exist_ok=True)
    atexit.register(shutil.rmtree, _tmp, True)

# Same reasoning as the root conftest's QT_QPA_PLATFORM default: there is no
# display in CI/sandboxed environments, so fall back to the "offscreen" Qt
# platform plugin unless something (a developer with a real display) already
# picked one.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

# main_window.py resolves its sibling imports (``widgets.*``, ``ui.*``) as
# top-level packages, the way they resolve when email_client/main.py is run
# directly (its own directory lands on sys.path as script dir). Mirror that
# here so ``ui.main_window`` imports cleanly for every test module below.
EMAIL_CLIENT_DIR = Path(__file__).resolve().parents[2] / "email_client"
if str(EMAIL_CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(EMAIL_CLIENT_DIR))


@pytest.fixture
def window(qtbot, monkeypatch, isolated_app_state):
    """Construct a real MainWindow with every singleton it can reach redirected
    away from this repo's real files.

    PySide6/pytest-qt are optional dependencies (not installed in every
    environment this repo's test suite runs in -- see requirements.txt). The
    ``qtbot`` parameter below is provided by the pytest-qt plugin itself, so
    if that plugin isn't installed, pytest fails resolving *this fixture's
    own parameter list* before a single line of its body runs -- no
    importorskip() placed inside this function's body could prevent that.
    That's why every test module using this fixture must call
    ``pytest.importorskip("pytestqt")`` and ``pytest.importorskip("PySide6")``
    at its own module level (as tests/ui/test_main_window_message_navigation.py
    does) -- that's evaluated at collection time, before any fixture in this
    file is ever requested, and cleanly skips just that module. The
    importorskip("PySide6") below is redundant defense-in-depth for the
    ``from ui.main_window import MainWindow`` line right after it, not the
    primary guard.

    MainWindow() normally touches two categories of persistent/global state:

    1. Config/auth singletons (EmailServerConfig, UnifiedEmailServer,
       SenderCategorizationManager, TokenManager) -- all created inside
       _load_config() from a real config.yaml. Stubbing _load_config() to a
       no-op means none of these are ever constructed in the first place, so
       there is nothing to isolate for them; self.config/self.server/
       self.sender_categorization simply stay None, which the UI logic
       under test doesn't touch. (This also sidesteps _load_config()'s real
       behavior of popping a blocking modal QMessageBox when config.yaml is
       missing but config.example.yaml is present -- true in this checkout
       -- which would otherwise hang the test on user input.)
    2. AppInfoCache (the module-level ``app_info_cache`` lazy singleton in
       email_server/utils/app_info_cache.py), reached via
       SmartMainWindow._post_init() -> restore_window_geometry() (fired by
       the QTimer.singleShot(0, ...) in MainWindow.__init__ once qtbot pumps
       the event loop, e.g. in qtbot.waitUntil()) and again via
       closeEvent() -> set_display_position()/set_virtual_screen_info()/
       store() (fired when qtbot.addWidget()'s automatic teardown closes
       this window). Both read/write through the *same* singleton instance
       cache (email_server.utils.app_info_cache._cache_instances), which is
       what the explicitly-requested ``isolated_app_state`` fixture redirects
       to a fresh per-test tmp_path directory and clears before and after
       every test. Depending on it here directly (rather than only relying
       on it being autouse) pins the fixture ordering pytest needs -- this
       fixture's isolation must be active for the *entire* lifetime of
       ``win``, including qtbot's post-test close() -- instead of leaving it
       to autouse-vs-explicit resolution order.
    """
    pytest.importorskip("PySide6")
    from ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_load_config", lambda self: None)

    win = MainWindow()
    qtbot.addWidget(win)

    yield win

    # _display_current_message() starts a real MessageBodyWorkerThread; make
    # sure it's finished before the window (and its QThread child) is torn
    # down, or Qt warns/crashes about destroying a running thread.
    body_thread = getattr(win, "body_worker_thread", None)
    if body_thread is not None:
        qtbot.waitUntil(lambda: not body_thread.isRunning(), timeout=2000)
