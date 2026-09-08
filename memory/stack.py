# -*- coding: utf-8 -*-
"""Контракт операторского стека: порты, профили, проверка файлов Compose, JSON SearXNG."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from memory.dashboard.config import DEFAULT_PORT
from memory.proxy.config import GATEWAY_PORT, PXPIPE_PORT


@dataclass(frozen=True)
class StackService:
    """Одна служба контракта. Валидатор и CLI читают только эти поля."""

    profile: Optional[str] = None
    host_port: Optional[int] = None
    bind: Optional[str] = None
    publish: bool = False
    image: Optional[str] = None
    runtime: str = "compose"
    json_format: Optional[bool] = None
    public_instance: Optional[bool] = None
    note: Optional[str] = None

    def publish_mapping(self) -> Optional[str]:
        """Ожидаемая запись ports: иначе не сверить bind из контракта с YAML."""
        if not self.publish or self.host_port is None:
            return None
        bind = self.bind or "127.0.0.1"
        return f"{bind}:{self.host_port}:{self.host_port}"

    def compose_profile_needles(self) -> Tuple[str, ...]:
        """Ключ profiles, не имя образа (research ⊂ local-deep-research)."""
        if not self.profile:
            return ()
        name = self.profile
        return (f'profiles: ["{name}"]', f"profiles: ['{name}']")


STACK: Dict[str, StackService] = {
    "searxng": StackService(
        profile="search",
        host_port=8080,
        bind="127.0.0.1",
        publish=True,
        image="docker.io/searxng/searxng:latest",
        runtime="compose",
        json_format=True,
        public_instance=False,
    ),
    "ldr": StackService(
        profile="research",
        host_port=5000,
        bind="127.0.0.1",
        publish=True,
        image="docker.io/localdeepresearch/local-deep-research:latest",
        runtime="compose",
    ),
    "ollama": StackService(
        profile="ollama",
        host_port=11434,
        publish=False,
        image="docker.io/ollama/ollama:latest",
        runtime="compose",
    ),
    "gateway": StackService(
        profile=None,
        host_port=GATEWAY_PORT,
        bind="127.0.0.1",
        publish=False,
        runtime="host",
        note="не в Compose по умолчанию: loopback peer (SR-04)",
    ),
    "dashboard": StackService(
        profile=None,
        host_port=DEFAULT_PORT,
        bind="127.0.0.1",
        publish=False,
        runtime="host",
    ),
    "pxpipe": StackService(
        profile=None,
        host_port=PXPIPE_PORT,
        bind="127.0.0.1",
        publish=False,
        runtime="host",
        note="imager не вендорится; шлюз фронтит pxpipe",
    ),
}

PROFILES = tuple(svc.profile for svc in STACK.values() if svc.profile)
DEFAULT_UP_PROFILES = (STACK["searxng"].profile,) if STACK["searxng"].profile else ()

ALLOWED_HOSTS = {
    "127.0.0.1",
    "localhost",
    "::1",
    "searxng",
    "host.docker.internal",
}


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
        if "host.docker.internal:host-gateway" not in text:
            errors.append("extra_hosts host.docker.internal required")
        if "no-new-privileges:true" not in text:
            errors.append("no-new-privileges required")
        for name, svc in STACK.items():
            needles = svc.compose_profile_needles()
            if needles and not any(n in text for n in needles):
                errors.append(f"profile missing:{svc.profile}")
            mapping = svc.publish_mapping()
            if mapping is not None and mapping not in text:
                errors.append(f"{name} must publish {svc.bind}:{svc.host_port}")
            # У демона нет аутентификации — порт на хост нельзя отдавать.
            if (
                not svc.publish
                and svc.runtime == "compose"
                and svc.host_port is not None
                and f"{svc.host_port}:{svc.host_port}" in text
            ):
                errors.append(f"{name} must not publish a host port")
            if svc.image and svc.image not in text:
                errors.append(f"image missing:{svc.image}")
    if settings.is_file():
        st = settings.read_text(encoding="utf-8")
        for name, svc in STACK.items():
            if svc.json_format is True and "- json" not in st:
                errors.append(f"{name} settings must enable json format")
            if svc.public_instance is False and "public_instance: false" not in st:
                errors.append(f"{name} must not be a public instance")
    return {"ok": not errors, "errors": errors, "root": str(root)}


def _default_searxng_url() -> str:
    svc = STACK["searxng"]
    bind = svc.bind or "127.0.0.1"
    return f"http://{bind}:{svc.host_port}"


def _base_url() -> str:
    return os.environ.get("AGENTIX_SEARXNG_URL", _default_searxng_url()).rstrip("/")


def _assert_local(base_url: str) -> None:
    # Control Plane тоже на loopback (:8112) — любой порт на 127.0.0.1 нельзя считать SearXNG.
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    expected = STACK["searxng"].host_port
    if host not in ALLOWED_HOSTS or port != expected:
        raise ValueError(
            f"SearXNG URL {base_url!r} is not loopback/compose-local on port {expected}; "
            f"set AGENTIX_SEARXNG_URL to {_default_searxng_url()}"
        )


def query(
    q: str,
    *,
    base_url: Optional[str] = None,
    limit: int = 8,
    timeout_s: float = 12.0,
    language: str = "en",
) -> List[Dict[str, Any]]:
    """Запрос format=json. Сеть упала — пустой список, не исключение."""
    base = (base_url or _base_url()).rstrip("/")
    _assert_local(base)
    q = (q or "").strip()
    if not q:
        return []
    params = urlencode(
        {
            "q": q,
            "format": "json",
            "language": language,
            "categories": "general",
        }
    )
    req = Request(
        f"{base}/search?{params}",
        headers={"Accept": "application/json", "User-Agent": "agentix-search/3.13"},
        method="GET",
    )
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
    except (URLError, HTTPError, TimeoutError, OSError):
        return []
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    hits: List[Dict[str, Any]] = []
    for item in data.get("results") or []:
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url or not title:
            continue
        hits.append(
            {
                "title": title,
                "url": url,
                "content": str(item.get("content") or "").strip(),
                "engine": str(item.get("engine") or ""),
            }
        )
        if len(hits) >= max(1, limit):
            break
    return hits


def _search_cli(args: argparse.Namespace) -> int:
    try:
        hits = query(args.q, base_url=args.base, limit=args.limit)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(hits, ensure_ascii=False, indent=2))
    else:
        for h in hits:
            print(f"{h['title']}\t{h['url']}")
        if not hits:
            print("no results (searxng down or empty)", file=sys.stderr)
    return 0


def cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Контракт операторского Compose-стека")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_c = sub.add_parser("contract", help="Порты и профили")
    p_c.add_argument("--json", action="store_true")
    sub.add_parser("check", help="Проверить файлы deploy/")
    p_s = sub.add_parser("search", help="Локальный SearXNG JSON")
    p_s.add_argument("--q", required=True)
    p_s.add_argument("--json", action="store_true")
    p_s.add_argument("--limit", type=int, default=8)
    p_s.add_argument("--base", default=None)
    args = parser.parse_args(argv)
    if args.cmd == "contract":
        payload = {
            "stack": {key: asdict(spec) for key, spec in STACK.items()},
            "profiles": list(PROFILES),
            "default_up": list(DEFAULT_UP_PROFILES),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for key, spec in STACK.items():
                bind = spec.bind or "-"
                print(f"{key}\t{bind}:{spec.host_port}\tprofile={spec.profile}")
        return 0
    if args.cmd == "search":
        return _search_cli(args)
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
