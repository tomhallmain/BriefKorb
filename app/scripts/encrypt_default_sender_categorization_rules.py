"""
Build the bundled encrypted sender-categorization rules blob.

Writes ``app/email_client/utils/data/sender_categorization_rules_default.enc``.

Source resolution (first match wins):

1. ``--input <path>`` — encrypt this file; also copies it to the **default**
   plaintext snapshot (``sender_categorization_rules.default.json``, gitignored)
   before encryption.

2. Else if ``sender_categorization_rules.active.json`` exists under
   ``email_client/utils/data/`` — copies **active → default** snapshot, then
   encrypts from that snapshot (so the bundled blob matches your active rules).

3. Else if ``sender_categorization_rules.default.json`` exists — encrypt that
   snapshot only.

``BRIEFKORB_SENDER_RULES_DEFAULT_JSON`` overrides the default snapshot path.

Usage (repo root, ``app`` on PYTHONPATH):

  python app/scripts/encrypt_default_sender_categorization_rules.py

  python app/scripts/encrypt_default_sender_categorization_rules.py --input path/to/rules.json

From ``app`` as cwd:

  python scripts/encrypt_default_sender_categorization_rules.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# Repo layout: BriefKorb/app/scripts/this_file.py
_APP_ROOT = Path(__file__).resolve().parents[1]
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

from email_client.utils.sender_categorization_rules import (
    LOCAL_ACTIVE_RULES_JSON,
    bundled_default_json_path,
)
from email_client.utils.sender_categorization_rules_codec import preprocess_data_for_encryption
from email_server.utils.constants import AppInfo
from email_server.utils.encryptor import symmetric_encrypt_data_to_file, symmetric_decrypt_data_from_file


_DEFAULT_OUTPUT = _APP_ROOT / "email_client" / "utils" / "data" / "sender_categorization_rules_default.enc"


def _passphrase() -> bytes:
    return (AppInfo.APP_IDENTIFIER + "_sender_rules").encode("utf-8")


def _pick_source(args: argparse.Namespace) -> Path:
    if args.input is not None:
        if not args.input.is_file():
            raise SystemExit(f"Input not found: {args.input}")
        return args.input
    if LOCAL_ACTIVE_RULES_JSON.is_file():
        return LOCAL_ACTIVE_RULES_JSON
    default_snap = bundled_default_json_path()
    if default_snap.is_file():
        return default_snap
    raise SystemExit(
        "No plaintext rules found. Use one of:\n"
        f"  --input path/to/rules.json\n"
        f"  create {LOCAL_ACTIVE_RULES_JSON}\n"
        f"  or create {bundled_default_json_path()}"
    )


def encrypt_file(input_path: Path, output_path: Path) -> None:
    text = input_path.read_text(encoding="utf-8")
    json.loads(text)  # validate
    payload = preprocess_data_for_encryption(text)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    symmetric_encrypt_data_to_file(payload, str(output_path), _passphrase(), compress=False)
    print(f"Wrote {output_path}")


def verify_round_trip(output_path: Path) -> None:
    from email_client.utils.sender_categorization_rules_codec import postprocess_data_from_decryption

    raw = symmetric_decrypt_data_from_file(str(output_path), _passphrase())
    postprocess_data_from_decryption(raw)
    print("Round-trip decrypt + postprocess OK")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Plain JSON rules file (optional if active or default snapshot exists)",
    )
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT, help="Encrypted output path")
    parser.add_argument("--verify-only", action="store_true", help="Verify existing output decrypts")
    args = parser.parse_args()

    if args.verify_only:
        verify_round_trip(args.output)
        return

    source = _pick_source(args)
    default_snapshot = bundled_default_json_path()

    if args.input is not None:
        shutil.copy2(source, default_snapshot)
        encrypt_from = default_snapshot
        print(f"Updated default snapshot from --input -> {default_snapshot}")
    elif source.resolve() == LOCAL_ACTIVE_RULES_JSON.resolve():
        shutil.copy2(source, default_snapshot)
        encrypt_from = default_snapshot
        print(f"Updated default snapshot from active JSON -> {default_snapshot}")
    else:
        encrypt_from = source

    encrypt_file(encrypt_from, args.output)
    verify_round_trip(args.output)


if __name__ == "__main__":
    main()
