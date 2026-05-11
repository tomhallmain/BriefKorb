"""Load sender impact heuristic marker lists.

Env: ``BRIEFKORB_SENDER_RULES_ACTIVE_JSON``, ``BRIEFKORB_SENDER_RULES_JSON`` (legacy), ``BRIEFKORB_SENDER_RULES_DEFAULT_JSON``, ``BRIEFKORB_SENDER_RULES_DEFAULT_ENC``.

Bootstrap writes missing ``active.json`` / ``default.json`` from the bundle unless ``BRIEFKORB_SKIP_SENDER_RULES_FILE_BOOTSTRAP`` is set.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Tuple

from email_server.utils.constants import AppInfo
from email_server.utils.encryptor import symmetric_decrypt_data_from_file

from email_client.utils.sender_categorization_rules_codec import postprocess_data_from_decryption

_DATA_DIR = Path(__file__).resolve().parent / "data"
_DEFAULT_ENC_PATH = _DATA_DIR / "sender_categorization_rules_default.enc"
_ACTIVE_JSON_PATH = _DATA_DIR / "sender_categorization_rules.active.json"
_DEFAULT_JSON_PATH = _DATA_DIR / "sender_categorization_rules.default.json"

# Exposed for the encrypt script (same paths as runtime checks).
LOCAL_ACTIVE_RULES_JSON = _ACTIVE_JSON_PATH

_RULE_KEYS = (
    "bulk_domain_markers",
    "bulk_subject_markers",
    "high_security_markers",
    "financial_inclusion_markers",
    "personal_mailbox_domains",
    "automation_local_markers",
    "promotional_local_markers",
    "low_impact_domain_parts",
    "low_impact_subject_terms",
)


@dataclass(frozen=True)
class SenderCategorizationRules:
    bulk_domain_markers: Tuple[str, ...]
    bulk_subject_markers: Tuple[str, ...]
    high_security_markers: Tuple[str, ...]
    financial_inclusion_markers: Tuple[str, ...]
    personal_mailbox_domains: Tuple[str, ...]
    automation_local_markers: Tuple[str, ...]
    promotional_local_markers: Tuple[str, ...]
    low_impact_domain_parts: Tuple[str, ...]
    low_impact_subject_terms: Tuple[str, ...]


def _read_rules_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if not str(k).startswith("_")}


def _load_bundled_encrypted_defaults() -> dict[str, Any]:
    env_path = os.environ.get("BRIEFKORB_SENDER_RULES_DEFAULT_ENC", "").strip()
    path = Path(env_path) if env_path else _DEFAULT_ENC_PATH
    if not path.is_file():
        return {}
    try:
        passphrase = (AppInfo.APP_IDENTIFIER + "_sender_rules").encode("utf-8")
        raw = symmetric_decrypt_data_from_file(str(path), passphrase)
        text = postprocess_data_from_decryption(raw)
        data = json.loads(text)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if not str(k).startswith("_")}


def _bootstrap_local_rule_snapshots_if_allowed() -> None:
    """Create gitignored active/default JSON from the bundled .enc when missing."""
    if os.environ.get("BRIEFKORB_SKIP_SENDER_RULES_FILE_BOOTSTRAP", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return
    if os.environ.get("BRIEFKORB_SENDER_RULES_ACTIVE_JSON", "").strip():
        return
    if os.environ.get("BRIEFKORB_SENDER_RULES_JSON", "").strip():
        return
    if os.environ.get("BRIEFKORB_SENDER_RULES_DEFAULT_JSON", "").strip():
        return

    bundled = _load_bundled_encrypted_defaults()
    if not bundled:
        return

    body: dict[str, Any] = {}
    for key in _RULE_KEYS:
        v = bundled.get(key)
        body[key] = list(v) if isinstance(v, list) else []

    doc = {
        "_comment": (
            "Auto-generated from bundled defaults on first rules load. "
            "Edit active.json; run encrypt_default_sender_categorization_rules.py before committing a new .enc."
        ),
        **body,
    }
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not _ACTIVE_JSON_PATH.is_file():
        _ACTIVE_JSON_PATH.write_text(text, encoding="utf-8")
    if not _DEFAULT_JSON_PATH.is_file():
        _DEFAULT_JSON_PATH.write_text(text, encoding="utf-8")


def _as_markers(value: Any) -> Tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(x).strip().lower() for x in value if str(x).strip())


def _rules_from_rule_dict(data: dict[str, Any]) -> SenderCategorizationRules:
    merged: dict[str, Tuple[str, ...]] = {}
    for key in _RULE_KEYS:
        merged[key] = _as_markers(data.get(key))
    return SenderCategorizationRules(
        bulk_domain_markers=merged["bulk_domain_markers"],
        bulk_subject_markers=merged["bulk_subject_markers"],
        high_security_markers=merged["high_security_markers"],
        financial_inclusion_markers=merged["financial_inclusion_markers"],
        personal_mailbox_domains=merged["personal_mailbox_domains"],
        automation_local_markers=merged["automation_local_markers"],
        promotional_local_markers=merged["promotional_local_markers"],
        low_impact_domain_parts=merged["low_impact_domain_parts"],
        low_impact_subject_terms=merged["low_impact_subject_terms"],
    )


def _resolve_active_json_path(rules_path: Path | None) -> Path:
    if rules_path is not None:
        return rules_path
    env_active = os.environ.get("BRIEFKORB_SENDER_RULES_ACTIVE_JSON", "").strip()
    if env_active:
        return Path(env_active)
    legacy = os.environ.get("BRIEFKORB_SENDER_RULES_JSON", "").strip()
    if legacy:
        return Path(legacy)
    return _ACTIVE_JSON_PATH


def bundled_default_json_path() -> Path:
    """Plaintext snapshot path for the encrypt script."""
    env_path = os.environ.get("BRIEFKORB_SENDER_RULES_DEFAULT_JSON", "").strip()
    return Path(env_path) if env_path else _DEFAULT_JSON_PATH


def load_sender_categorization_rules(
    *,
    rules_path: Path | None = None,
) -> SenderCategorizationRules:
    """Active JSON if present, else decrypted bundle."""

    if rules_path is None:
        _bootstrap_local_rule_snapshots_if_allowed()

    active_path = _resolve_active_json_path(rules_path)
    if active_path.is_file():
        raw = _read_rules_json(active_path)
        return _rules_from_rule_dict(raw)

    bundled = _load_bundled_encrypted_defaults()
    return _rules_from_rule_dict(bundled)
