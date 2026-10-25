# TOOLS REGISTRY — Progressive index (v3.13+)

**Do not paste this entire file into the model.**  
Load only what you need via:

```bash
python tools/select.py --intent git|test|memory|docker|state|handoff|bootstrap|xai
# operator:
python -m memory doctor
python -m memory.local_mcp list-tools   # stdlib MCP (proxy_health, state_*, …)
```

Host agent tools (`read_file`, `search_replace`, `run_terminal_command`, `grep`, MCP, …) use **exact schemas from the host environment** — do not guess parameter names. Prefer attaching `agentix-local-mcp` when the host supports MCP instead of guessing argv from Markdown blocks.

## Index

| Intent | When | Blocks |
|--------|------|--------|
| `bootstrap` | Cycle start / new worktree | OS bootstrap + Agent-Init |
| `state` | Before PLAN / after Reviewer | Bounded LOOP_STATE CLI |
| `memory` | Orchestrator snapshot / Reviewer merge | memory query/update/meta/playbooks |
| `git` | Before push/PR/merge | preflight_git + sync |
| `test` | Tester role | venv + pytest |
| `docker` | Operator stack (SearXNG / LDR) | deploy/compose + agentix-stack.sh |
| `xai` | Mix xAI server-side tools with local client-side Tools | tools/blocks/common/xai_tools.md |
| `handoff` | Every role exit | validate_handoff rules |
| `doctor` | Live verify / cold operator check | `memory.doctor` + [live-verify](docs/live-verify.md) |
| `mcp` | Schema-backed harness tools | `memory.local_mcp` / `agentix-local-mcp` |
| `parallel` | Multi-stream owned_paths | `scripts/parallel_scaffold.sh` + leases |

## Rules

1. Prefer `scripts/*.sh` over multi-step interactive rituals.  
2. Prefer `.venv` interpreter.  
3. Prefer `memory state snapshot` over reading `.agent` dumps.  
4. Full multi-repo `gh` verbatim blocks are **opt-in** (`STRICT_MULTI_REPO=1` or `project_config.git.strict_multi_repo`).  
5. UTF-8 for all handoff and log files.

**Template version:** see `VERSION` (3.14.0+). Live leader adapter is Grok+pxpipe; do not use Blackbox for new routing.
