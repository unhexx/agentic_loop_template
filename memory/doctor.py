# -*- coding: utf-8 -*-
"""Operator doctor: pxpipe/gateway, port ledger, stack contract, delegate env (no shims)."""

from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# Documented Agentix loopback ports (docs/proxy.md, docs/stack.md).
_PORT_LEDGER = (
    ("pxpipe", 8100),
    ("pxpipe_agy_optional", 8101),
    ("gateway", 8110),
    ("control_plane", 8112),
    ("searxng", 8080),
    ("ldr_research", 5000),
)


def _tcp_ok(host: str, port: int, timeout_s: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _port_ledger(host: str = "127.0.0.1") -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name, port in _PORT_LEDGER:
        listening = _tcp_ok(host, port)
        rows.append(
            {
                "name": name,
                "host": host,
                "port": port,
                "listening": listening,
                "required_for_live_grok": name in {"pxpipe", "gateway"},
            }
        )
    return rows


def _delegate_env() -> Dict[str, Any]:
    """P10-safe surface: report Gemini key presence; never set GOOGLE_GEMINI_BASE_URL."""
    key = os.environ.get("GEMINI_API_KEY") or ""
    base = os.environ.get("GOOGLE_GEMINI_BASE_URL")
    return {
        "gemini_api_key_set": bool(key.strip()),
        "google_gemini_base_url_set": bool(base and str(base).strip()),
        "p10_safe": not bool(base and str(base).strip()),
        "note": (
            "P10 delegate must use GEMINI_API_KEY directly; "
            "do not point GOOGLE_GEMINI_BASE_URL at pxpipe/agy-pxpipe"
        ),
    }


def doctor_report(
    workdir: Optional[Path] = None,
    *,
    frontend: Optional[str] = None,
    include_stack: bool = True,
) -> Dict[str, Any]:
    """Aggregate operator checks. Does not restart pxpipe or invent secrets."""
    from memory.proxy.health import health_report

    workdir = Path(workdir).resolve() if workdir is not None else Path.cwd().resolve()
    proxy = health_report(workdir, strict=False, frontend=frontend)
    ports = _port_ledger()
    stack: Dict[str, Any] | None = None
    if include_stack:
        try:
            from memory.stack import validate_stack_files

            if not (workdir / "deploy" / "compose.yaml").is_file():
                stack = {
                    "ok": False,
                    "errors": [f"missing:deploy/compose.yaml under {workdir}"],
                }
            else:
                stack = validate_stack_files(workdir)
        except Exception as exc:
            stack = {"ok": False, "error": str(exc)}

    required_ports_ok = all(
        row["listening"] for row in ports if row["required_for_live_grok"]
    )
    findings: List[Dict[str, str]] = []
    if not proxy.get("ok"):
        findings.append(
            {
                "severity": "environment",
                "code": "proxy_not_ready",
                "detail": "memory.proxy health reports ok=false (TCP/mode)",
            }
        )
    if proxy.get("ok") and not required_ports_ok:
        findings.append(
            {
                "severity": "environment",
                "code": "required_port_down",
                "detail": "pxpipe :8100 or gateway :8110 not listening",
            }
        )
    delegate = _delegate_env()
    if not delegate["p10_safe"]:
        findings.append(
            {
                "severity": "config",
                "code": "gemini_base_url_set",
                "detail": "GOOGLE_GEMINI_BASE_URL is set; unset for P10-compliant agy",
            }
        )
    if include_stack and isinstance(stack, dict) and not stack.get("ok"):
        findings.append(
            {
                "severity": "product",
                "code": "stack_contract",
                "detail": "deploy/ compose contract failed validate_stack_files",
            }
        )

    ok = bool(proxy.get("ok")) and required_ports_ok and delegate["p10_safe"]
    if include_stack and isinstance(stack, dict):
        ok = ok and bool(stack.get("ok"))

    return {
        "ok": ok,
        "workdir": str(workdir),
        "proxy": proxy,
        "ports": ports,
        "stack": stack,
        "delegate_env": delegate,
        "findings": findings,
        "guidance": [
            "Classify live verify failures as pass / environment-blocked / product-bug",
            "Do not restart pxpipe.service during verify rituals",
            "Do not invent API keys; 401 on :8110/:8100 without bearer is environment",
        ],
    }


def cli(argv: Optional[Sequence[str]] = None) -> int:
    from memory.logutil import configure_logging

    configure_logging()
    parser = argparse.ArgumentParser(description="Agentix operator doctor")
    parser.add_argument("--workdir", type=Path, default=None)
    parser.add_argument("--frontend", default=None)
    parser.add_argument("--no-stack", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Exit 1 when ok is false")
    parser.add_argument("--json", action="store_true", default=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = doctor_report(
        args.workdir,
        frontend=args.frontend,
        include_stack=not args.no_stack,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and not report.get("ok"):
        return 1
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    return cli(argv)


if __name__ == "__main__":
    raise SystemExit(cli())
