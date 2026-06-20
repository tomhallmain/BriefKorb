#!/usr/bin/env python3
"""Wipe the entity graph data files (entity_graph.nq and resolution_queue.json).

Usage
-----
  python scripts/reset_entity_graph.py           # uses config.yaml storage path
  python scripts/reset_entity_graph.py --yes     # skip the confirmation prompt
  python scripts/reset_entity_graph.py /path/to/entity_graph --yes
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_FILES = ("entity_graph.nq", "resolution_queue.json")


def resolve_dir(override: str | None) -> Path:
    if override:
        return Path(override)
    config_path = Path(__file__).parent.parent / "email_server" / "config.yaml"
    from email_server.config import EmailServerConfig
    cfg = EmailServerConfig.from_file(str(config_path))
    return Path(cfg.token_storage_path) / "entity_graph"


def main() -> None:
    parser = argparse.ArgumentParser(description="Wipe the entity graph storage files.")
    parser.add_argument("storage_dir", nargs="?", help="Override storage directory path")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    try:
        storage_dir = resolve_dir(args.storage_dir)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    targets = [storage_dir / f for f in _FILES]
    existing = [t for t in targets if t.exists()]

    if not existing:
        print(f"Nothing to delete — no entity graph files found in {storage_dir}")
        return

    print("The following files will be deleted:")
    for t in existing:
        size = t.stat().st_size
        print(f"  {t}  ({size:,} bytes)")

    if not args.yes:
        answer = input("\nContinue? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

    for t in existing:
        t.unlink()
        print(f"Deleted {t.name}")

    print("Entity graph reset.")


if __name__ == "__main__":
    main()
