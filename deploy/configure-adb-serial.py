#!/usr/bin/env python3
"""Apply an explicitly configured ADB serial to existing and future profiles."""

import argparse
import json
import os
from pathlib import Path


def update_file(path, serial):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict) or not isinstance(data.get("Alas"), dict):
        return False
    emulator = data["Alas"].setdefault("Emulator", {})
    if not isinstance(emulator, dict):
        return False
    if emulator.get("Serial") == serial:
        return False
    emulator["Serial"] = serial
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--serial", required=True)
    args = parser.parse_args()
    changed = sum(update_file(path, args.serial) for path in args.config_dir.glob("*.json"))
    print(f"ADB Serial configured for {changed} profile template(s)", flush=True)


if __name__ == "__main__":
    main()
