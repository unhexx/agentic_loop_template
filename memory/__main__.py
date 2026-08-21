"""CLI: python -m memory {info|snapshot|query|update|state|...}."""

from __future__ import annotations

import argparse
import json
import sys


def _cli() -> None:
    # Early dispatch for nested modules so `python -m memory state ...` works
    if len(sys.argv) > 1 and sys.argv[1] == "state":
        from .state import cli as state_cli

        raise SystemExit(state_cli(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "supervisor":
        from .supervisor import main

        raise SystemExit(main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "dashboard":
        from .dashboard.__main__ import main as dashboard_main

        raise SystemExit(dashboard_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] in {
        "experience",
        "harvest-experience",
        "experience_harvester",
    }:
        from .experience_harvester import cli as exp_cli

        raise SystemExit(exp_cli(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "compressor":
        from .compressor import cli as compressor_cli

        raise SystemExit(compressor_cli(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] in {"context-budget", "context_budget"}:
        from .context_budget import cli as budget_cli

        raise SystemExit(budget_cli(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "knowledge":
        from .knowledge import cli as knowledge_cli

        raise SystemExit(knowledge_cli(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "proxy":
        from .proxy.__main__ import cli as proxy_cli

        raise SystemExit(proxy_cli(sys.argv[2:]))

    parser = argparse.ArgumentParser(description="Agentic Loop structured memory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="Workspace id and paths")
    sub.add_parser("snapshot", help="Full memory snapshot (JSON)")

    qp = sub.add_parser("query", help="Query patterns")
    qp.add_argument("--category", default=None)
    qp.add_argument("--top", type=int, default=5)
    qp.add_argument("--contains", default=None)

    up = sub.add_parser("update", help="Add patterns")
    up.add_argument("--json", default=None)
    up.add_argument("--category", default="Common Failure Patterns")
    up.add_argument("--description", default=None)

    # placeholders for discoverability
    sub.add_parser("state", help="Bounded LOOP_STATE (use: python -m memory state snapshot)")

    args = parser.parse_args()

    from .store import query_memory, snapshot, update_from_json_payload, update_memory
    from .workspace import get_workspace_id, memory_paths

    if args.cmd == "info":
        paths = memory_paths()
        print(
            json.dumps(
                {
                    "workspace_id": get_workspace_id(),
                    "dir": str(paths["dir"]),
                    "file": str(paths["file"]),
                    "lock": str(paths["lock"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.cmd == "snapshot":
        print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
    elif args.cmd == "query":
        rows = query_memory(
            category=args.category,
            top_n=args.top,
            contains=args.contains,
        )
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif args.cmd == "update":
        if args.json:
            result = update_from_json_payload(args.json)
        elif args.description:
            result = update_memory(
                new_patterns=[{"category": args.category, "description": args.description}]
            )
        else:
            print("Need --json or --description", file=sys.stderr)
            sys.exit(2)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.cmd == "state":
        print("Use: python -m memory state snapshot|compact|init|...", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    _cli()
