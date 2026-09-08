# -*- coding: utf-8 -*-
"""Поиск через локальный SearXNG JSON. Без ключей, fail-open."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

DEFAULT_BASE = "http://127.0.0.1:8080"
ALLOWED_HOSTS = {
    "127.0.0.1",
    "localhost",
    "::1",
    "searxng",
    "host.docker.internal",
}


def _base_url() -> str:
    return os.environ.get("AGENTIX_SEARXNG_URL", DEFAULT_BASE).rstrip("/")


def _assert_local(base_url: str) -> None:
    host = (urlparse(base_url).hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError(
            f"SearXNG URL host {host!r} is not loopback/compose-local; "
            "set AGENTIX_SEARXNG_URL to http://127.0.0.1:8080"
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


def cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Локальный SearXNG JSON")
    parser.add_argument("--q", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--base", default=None)
    args = parser.parse_args(argv)
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


def main() -> None:
    raise SystemExit(cli())


if __name__ == "__main__":
    main()
