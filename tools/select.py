#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Progressive disclosure: print only the tool blocks needed for an intent + OS."""

from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BLOCKS = ROOT / "blocks"

INTENTS = {
    "git": ["common/git_preflight.md", "linux/git_sync.md", "windows/git_sync.md"],
    "test": ["linux/python_venv.md", "windows/python_venv.md", "common/pytest.md"],
    "memory": ["common/memory.md"],
    "docker": ["common/docker.md"],
    "state": ["common/state.md"],
    "handoff": ["common/handoff.md"],
    "bootstrap": ["linux/bootstrap.md", "windows/bootstrap.md"],
}


def detect_os() -> str:
    sysname = platform.system().lower()
    if sysname.startswith("win"):
        return "windows"
    return "linux"


def resolve_paths(intent: str, os_name: str) -> list[Path]:
    rels = INTENTS.get(intent, [])
    out: list[Path] = []
    for rel in rels:
        if rel.startswith("linux/") and os_name != "linux":
            continue
        if rel.startswith("windows/") and os_name != "windows":
            continue
        p = BLOCKS / rel
        if p.is_file():
            out.append(p)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Select tool instruction blocks")
    parser.add_argument(
        "--intent",
        required=True,
        choices=sorted(INTENTS.keys()),
        help="What you are about to do",
    )
    parser.add_argument("--os", default="auto", choices=["auto", "linux", "windows"])
    parser.add_argument("--list", action="store_true", help="List paths only")
    args = parser.parse_args()

    os_name = detect_os() if args.os == "auto" else args.os
    paths = resolve_paths(args.intent, os_name)
    if not paths:
        print(f"# No blocks for intent={args.intent} os={os_name}", file=sys.stderr)
        return 1
    if args.list:
        for p in paths:
            print(p.relative_to(ROOT))
        return 0
    print(f"# tools/select intent={args.intent} os={os_name}\n")
    for p in paths:
        print(f"## {p.relative_to(ROOT)}\n")
        print(p.read_text(encoding="utf-8").rstrip())
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
