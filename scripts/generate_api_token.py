#!/usr/bin/env python3
"""
Generate and register a bearer token for BriefKorb's external-facing API
(validated by app/django_app/authentication.py's require_external_api_token).

The desktop and web Settings pages (External API tab) can also generate and
revoke these tokens. This script is the command-line equivalent: it edits
`external_api.tokens` in email_server/config.yaml.

Usage:
    python scripts/generate_api_token.py --label tagesform
    python scripts/generate_api_token.py --label tagesform --no-enable
    python scripts/generate_api_token.py --list
"""

import argparse
import shutil
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP_DIR))

from email_server.config import EmailServerConfig  # noqa: E402


def _load_config(config_path: Path) -> EmailServerConfig:
    if not config_path.exists():
        sys.exit(
            f"No config file at {config_path}.\n"
            f"Copy app/email_server/config.example.yaml to that path and "
            f"fill in your Microsoft/Gmail settings first."
        )
    return EmailServerConfig.from_file(str(config_path))


def _list_tokens(config: EmailServerConfig) -> None:
    if not config.external_api.tokens:
        print("No external API tokens registered.")
        return
    print(f"external_api.enabled: {config.external_api.enabled}")
    for i, t in enumerate(config.external_api.tokens, 1):
        print(f"  {i}. label={t.display_label()}  token={t.masked_token()}")


def _generate_token(config: EmailServerConfig, config_path: Path, label: str, nbytes: int, enable: bool) -> str:
    if label and any(t.label == label for t in config.external_api.tokens):
        print(f"Warning: label '{label}' is already in use by another token.", file=sys.stderr)

    was_enabled = config.external_api.enabled
    token = config.external_api.generate_token(label=label, nbytes=nbytes, enable=enable)
    if enable and not was_enabled:
        print("external_api.enabled was false -- turning it on.")

    # EmailServerConfig.save() rewrites the whole file via to_dict(), which
    # only knows about the fields EmailServerConfig models -- comments and
    # any unrecognized top-level keys in the existing config.yaml won't
    # survive the round trip. Back up first so that's recoverable.
    backup_path = config_path.with_name(config_path.name + ".bak")
    shutil.copyfile(config_path, backup_path)
    config.save(str(config_path))
    return token


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--label", default="",
        help="Free-text label for the token (e.g. the consumer's name, like 'tagesform'). Used only for logging.",
    )
    parser.add_argument(
        "--length", type=int, default=32,
        help="Random bytes of entropy for the token (default: 32).",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="Path to config.yaml (default: resolved the same way the app resolves it, including BRIEFKORB_CONFIG_PATH).",
    )
    parser.add_argument(
        "--no-enable", action="store_true",
        help="Don't flip external_api.enabled to true if it's currently false.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List registered tokens (labels only, tokens truncated) and exit without generating anything.",
    )
    args = parser.parse_args()

    config_path = args.config or EmailServerConfig.resolve_path(APP_DIR)
    config = _load_config(config_path)

    if args.list:
        _list_tokens(config)
        return

    token = _generate_token(config, config_path, args.label, args.length, enable=not args.no_enable)

    print(f"Token generated and saved to {config_path}")
    print(f"(previous config backed up to {config_path.name}.bak)")
    print()
    print("This value is shown once -- store it in tagesform's BriefKorb integration config now:")
    print()
    print(token)


if __name__ == "__main__":
    main()
