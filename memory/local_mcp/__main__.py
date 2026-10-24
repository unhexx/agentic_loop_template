# -*- coding: utf-8 -*-
"""CLI: python -m memory.local_mcp {serve|list-tools|probe|run|jenkins-relay}."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="memory.local_mcp")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="Newline-delimited JSON-RPC on stdin/stdout")
    sub.add_parser("list-tools", help="Print {\"tools\": [...]} and exit")
    probe = sub.add_parser("probe", help="Read-only reachability check")
    probe.add_argument(
        "--backend",
        required=True,
        choices=("act", "self_hosted", "jenkins"),
    )
    probe.add_argument("--workdir", default=".")
    run = sub.add_parser("run", help="Run the local pipeline and print result JSON")
    run.add_argument("--backend", choices=("act", "self_hosted", "jenkins"))
    run.add_argument("--workdir", default=".")
    run.add_argument("--workflow-file", default=None)
    run.add_argument("--job-name", default=None)
    run.add_argument("--timeout-s", type=int, default=None)
    run.add_argument("--runner-label", action="append", default=None)
    run.add_argument("--job-path", default=None)
    run.add_argument("--jenkinsfile", default=None)
    sub.add_parser("jenkins-relay", help="Loopback Jenkins webhook checker")
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 2
    if args.cmd == "serve":
        from memory.local_mcp.server import serve

        return serve()
    if args.cmd == "list-tools":
        from memory.local_mcp.registry import catalog_tools

        sys.stdout.write(
            json.dumps(
                {"tools": catalog_tools()},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        sys.stdout.write("\n")
        return 0
    if args.cmd == "probe":
        return _probe_cli(args.backend, args.workdir)
    if args.cmd == "run":
        return _run_cli(args)
    if args.cmd == "jenkins-relay":
        from memory.local_mcp.hmac_relay import main as relay_main

        return relay_main()
    sys.stderr.write(f"{args.cmd} is a later PR\n")
    return 2


def _probe_cli(backend: str, workdir: str) -> int:
    from memory.local_mcp.probe import probe_backend
    from memory.local_mcp.validate import result_errors

    result = probe_backend(backend, Path(workdir))
    problems = result_errors(result)
    if problems:
        raise RuntimeError("probe result failed schema: " + "; ".join(problems))
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    return int(result["exit_code"])


def _allow_root() -> Path:
    """Absolute AGENTIX_PROJECT_ROOT, otherwise the process cwd."""
    env_root = os.environ.get("AGENTIX_PROJECT_ROOT")
    if isinstance(env_root, str) and Path(env_root).is_absolute():
        return Path(env_root).resolve()
    return Path.cwd().resolve()


def _contained_workdir(raw: str, allow_root: Path) -> Path | None:
    """Directory inside allow_root. None means do not open project config."""
    if not raw or "\x00" in raw:
        return None
    try:
        root = Path(allow_root).resolve()
        resolved = Path(raw).resolve()
    except (OSError, ValueError):
        return None
    if resolved != root and root not in resolved.parents:
        return None
    try:
        if not resolved.is_dir():
            return None
    except (OSError, ValueError):
        return None
    return resolved


def _empty_flag(args: argparse.Namespace) -> bool:
    named = (
        args.backend,
        args.workdir,
        args.workflow_file,
        args.job_name,
        args.job_path,
        args.jenkinsfile,
    )
    if any(value == "" for value in named):
        return True
    labels = args.runner_label or []
    return any(item == "" for item in labels)


def _default_backend(workdir: str) -> str:
    contained = _contained_workdir(workdir, _allow_root())
    if contained is None:
        return "act"
    from memory.local_mcp.config import load_local_pipeline

    return load_local_pipeline(contained).config_backend


def _run_arguments(args: argparse.Namespace) -> dict:
    backend = args.backend if args.backend is not None else _default_backend(args.workdir)
    arguments: dict = {
        "backend": backend,
        "working_directory": args.workdir,
    }
    if args.workflow_file is not None:
        arguments["workflow_file"] = args.workflow_file
    if args.job_name is not None:
        arguments["job_name"] = args.job_name
    if args.timeout_s is not None:
        arguments["timeout_s"] = args.timeout_s
    if args.runner_label:
        arguments["runner_labels"] = list(args.runner_label)
    if args.job_path is not None:
        arguments["job_path"] = args.job_path
    if args.jenkinsfile is not None:
        arguments["jenkinsfile_path"] = args.jenkinsfile
    return arguments


def _run_cli(args: argparse.Namespace) -> int:
    # An empty flag is exit 2. It is not backend_field_mismatch.
    if _empty_flag(args):
        sys.stderr.write("empty argument\n")
        return 2
    from memory.local_mcp.pipeline import run_local_pipeline
    from memory.local_mcp.validate import argument_errors, result_errors

    arguments = _run_arguments(args)
    if argument_errors("run_local_pipeline", arguments):
        sys.stderr.write("invalid arguments\n")
        return 2
    result = run_local_pipeline(arguments, allow_root=_allow_root())
    problems = result_errors(result)
    if problems:
        raise RuntimeError("run result failed schema: " + "; ".join(problems))
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
