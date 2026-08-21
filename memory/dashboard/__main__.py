# -*- coding: utf-8 -*-
"""Точка входа: python -m memory.dashboard serve --workdir PATH --port 8112."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memory.dashboard",
        description="Панель оператора: loopback HTTP",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("serve", help="Поднять uvicorn на 127.0.0.1:8112")
    sp.add_argument("--workdir", type=Path, default=None, help="Корень с .agent/")
    sp.add_argument("--port", type=int, default=None, help="Порт (по умолчанию 8112)")
    sp.add_argument("--host", default=None, help="Bind (только loopback)")

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.cmd == "serve":
        try:
            from memory.dashboard.server import BindError, serve
        except ImportError:
            print(
                "нужен extra: pip install -r requirements-dashboard.txt",
                file=sys.stderr,
            )
            return 2
        try:
            serve(workdir=args.workdir, host=args.host, port=args.port)
        except BindError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            return 0
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
