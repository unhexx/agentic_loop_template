# -*- coding: utf-8 -*-
"""CLI: python -m memory.proxy {health|install-venv|install-host|serve|stats}."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cli(argv: Optional[Sequence[str]] = None) -> int:
    from memory.logutil import configure_logging

    configure_logging()
    parser = argparse.ArgumentParser(description="Прокси запросов Agentix (политика + health)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ph = sub.add_parser("health", help="Проба pxpipe/шлюза")
    ph.add_argument("--strict", action="store_true", help="Код 1, если pxpipe молчит")
    ph.add_argument("--json", action="store_true", help="Печатать JSON (по умолчанию тоже JSON)")
    ph.add_argument("--init", action="store_true", help="Правило Init: mock не валит bootstrap")
    ph.add_argument("--frontend", default=None, help="Подсказка мастера: grok|cursor|blackbox|mock")
    ph.add_argument("--workdir", type=Path, default=None)

    piv = sub.add_parser("install-venv", help="Дописать export'ы в .venv/bin/activate")
    piv.add_argument("--root", type=Path, default=None)
    piv.add_argument("--url", default=None, help="GROK_CLI_CHAT_PROXY_BASE_URL")

    pih = sub.add_parser("install-host", help="Опционально прописать ~/.grok/config.toml")
    pih.add_argument("--config", type=Path, default=None)
    pih.add_argument("--url", default=None)
    pih.add_argument("--dry-run", action="store_true")

    ps = sub.add_parser("serve", help="Шлюз :8110 → pxpipe :8100")
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--port", type=int, default=8110)
    ps.add_argument("--upstream", default=None, help="База pxpipe, по умолчанию из конфига")
    ps.add_argument("--workdir", type=Path, default=None)
    pst = sub.add_parser("stats", help="Сводка токенов (pxpipe + JSONL + компрессор)")
    pst.add_argument("--json", action="store_true")
    pst.add_argument("--cycle", type=int, default=None)
    pst.add_argument("--workdir", type=Path, default=None)

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.cmd == "health":
        from memory.proxy.health import health_report
        from memory.proxy.policy import init_should_fail

        workdir = args.workdir
        report = health_report(workdir, strict=args.strict, frontend=args.frontend)
        _print(report)
        if args.init:
            return 1 if init_should_fail(workdir, frontend=args.frontend) else 0
        if args.strict and not report.get("ok"):
            return 1
        return 0

    if args.cmd == "install-venv":
        from memory.proxy.install import install_venv

        report = install_venv(root=args.root, chat_proxy_url=args.url)
        _print(report)
        return 0 if report.get("ok") else 1

    if args.cmd == "install-host":
        from memory.proxy.install import install_host

        report = install_host(config_path=args.config, url=args.url, dry_run=args.dry_run)
        _print(report)
        return 0 if report.get("ok") else 1

    if args.cmd == "serve":
        from memory.proxy.gateway import BindError, serve

        try:
            serve(
                host=args.host,
                port=int(args.port),
                upstream=args.upstream,
                workdir=args.workdir,
            )
        except BindError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            return 0
        return 0

    if args.cmd == "stats":
        from memory.proxy.stats import collect_stats

        report = collect_stats(args.workdir)
        if args.cycle is not None:
            report["cycle"] = args.cycle
        _print(report)
        return 0

    return 2


def main(argv: Optional[Sequence[str]] = None) -> int:
    return cli(argv)


if __name__ == "__main__":
    raise SystemExit(cli())
