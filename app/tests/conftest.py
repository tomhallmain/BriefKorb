import sys
from pathlib import Path
import types


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
