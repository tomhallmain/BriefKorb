"""
conftest for tests/unit/.

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
"""

import os

if "BRIEFKORB_CACHE_DIR" not in os.environ:
    import tempfile
    import atexit
    import shutil

    _tmp = tempfile.mkdtemp(prefix="briefkorb_unit_tests_")
    os.environ["BRIEFKORB_CACHE_DIR"] = os.path.join(_tmp, "cache")
    os.environ["BRIEFKORB_LOG_DIR"] = os.path.join(_tmp, "logs")
    os.environ["BRIEFKORB_CONFIG_PATH"] = os.path.join(_tmp, "config.yaml")
    os.environ["BRIEFKORB_TOKEN_STORAGE_PATH"] = os.path.join(_tmp, "tokens")
    os.makedirs(os.environ["BRIEFKORB_CACHE_DIR"], exist_ok=True)
    os.makedirs(os.environ["BRIEFKORB_LOG_DIR"], exist_ok=True)
    os.makedirs(os.environ["BRIEFKORB_TOKEN_STORAGE_PATH"], exist_ok=True)
    atexit.register(shutil.rmtree, _tmp, True)
