# -*- coding: utf-8 -*-
"""Контракт операторского стека: порты, профили, проверка файлов Compose."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

STACK: Dict[str, Dict[str, Any]] = {
    "searxng": {
        "profile": "search",
        "host_port": 8080,
        "bind": "127.0.0.1",
        "image": "docker.io/searxng/searxng:latest",
    },
    "ldr": {
        "profile": "research",
        "host_port": 5000,
        "bind": "127.0.0.1",
        "image": "docker.io/localdeepresearch/local-deep-research:latest",
    },
    "ollama": {
        "profile": "ollama",
        "host_port": None,
        "publish": False,
        "image": "docker.io/ollama/ollama:latest",
    },
    "gateway": {
        "profile": None,
        "host_port": 8110,
        "bind": "127.0.0.1",
        "runtime": "host",
        "note": "не в Compose по умолчанию: loopback peer (SR-04)",
    },
    "dashboard": {
        "profile": None,
        "host_port": 8112,
        "bind": "127.0.0.1",
        "runtime": "host",
    },
    "pxpipe": {
        "profile": None,
        "host_port": 8100,
        "bind": "127.0.0.1",
        "runtime": "host",
        "note": "imager не вендорится; шлюз фронтит pxpipe",
    },
}

PROFILES = ("search", "research", "ollama")
DEFAULT_UP_PROFILES = ("search",)


def repo_root(start: Optional[Path] = None) -> Path:
    here = Path(start or Path(__file__).resolve()).resolve()
    if here.is_file():
        here = here.parent
    for p in (here, *here.parents):
        if (p / "deploy" / "compose.yaml").is_file() and (p / "VERSION").is_file():
            return p
    return Path.cwd()


def compose_path(root: Optional[Path] = None) -> Path:
    return repo_root(root) / "deploy" / "compose.yaml"


def validate_stack_files(root: Optional[Path] = None) -> Dict[str, Any]:
    """Проверяем файлы без docker daemon."""
    root = repo_root(root)
    errors: List[str] = []
    compose = root / "deploy" / "compose.yaml"
    settings = root / "deploy" / "searxng" / "settings.yml"
    limiter = root / "deploy" / "searxng" / "limiter.toml"
    env_ex = root / "deploy" / "compose.env.example"
    script = root / "scripts" / "agentix-stack.sh"
    for p in (compose, settings, limiter, env_ex, script):
        if not p.is_file():
            errors.append(f"missing:{p.relative_to(root)}")
    if compose.is_file():
        text = compose.read_text(encoding="utf-8")
        if "127.0.0.1:8080:8080" not in text:
            errors.append("searxng must publish 127.0.0.1:8080")
        if "127.0.0.1:5000:5000" not in text:
            errors.append("ldr must publish 127.0.0.1:5000")
        if "11434:11434" in text:
            errors.append("ollama must not publish a host port")
        if "host.docker.internal:host-gateway" not in text:
            errors.append("extra_hosts host.docker.internal required")
        if "no-new-privileges:true" not in text:
            errors.append("no-new-privileges required")
        for name in PROFILES:
            if name not in text:
                errors.append(f"profile missing:{name}")
    if settings.is_file():
        st = settings.read_text(encoding="utf-8")
        if "json" not in st:
            errors.append("searxng settings must enable json format")
        if "public_instance: false" not in st:
            errors.append("searxng must not be a public instance")
    return {"ok": not errors, "errors": errors, "root": str(root)}


def cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Контракт операторского Compose-стека")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_c = sub.add_parser("contract", help="Порты и профили")
    p_c.add_argument("--json", action="store_true")
    sub.add_parser("check", help="Проверить файлы deploy/")
    args = parser.parse_args(argv)
    if args.cmd == "contract":
        payload = {"stack": STACK, "profiles": list(PROFILES), "default_up": list(DEFAULT_UP_PROFILES)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for key, spec in STACK.items():
                port = spec.get("host_port")
                print(f"{key}\t{spec.get('bind', '-')}:{port}\tprofile={spec.get('profile')}")
        return 0
    report = validate_stack_files()
    if not report["ok"]:
        print(json.dumps(report, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


def main() -> None:
    raise SystemExit(cli())


if __name__ == "__main__":
    main()
