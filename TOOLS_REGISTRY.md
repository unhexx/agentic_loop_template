# TOOLS REGISTRY — Progressive index (v3.3)

**Do not paste this entire file into the model.**  
Load only what you need via:

```bash
python tools/select.py --intent git|test|memory|docker|state|handoff|bootstrap
```

## Index

| Intent | When | Blocks |
|--------|------|--------|
| `bootstrap` | Cycle start / new worktree | OS bootstrap + Agent-Init |
| `state` | Before PLAN / after Reviewer | Bounded LOOP_STATE CLI |
| `memory` | Orchestrator snapshot / Reviewer merge | memory query/update/meta |
| `git` | Before push/PR/merge | preflight_git + sync |
| `test` | Tester role | venv + pytest |
| `docker` | Compose-based projects | compose basics |
| `handoff` | Every role exit | validate_handoff rules |

## Core runner tools (environment)

Use the host agent tools (`read_file`, `search_replace`, `run_terminal_command`, `grep`, `spawn_subagent`, …) with **exact schemas from the host**. This registry only documents **project harness** helpers.

## Rules

1. Prefer `scripts/*.sh` over multi-step interactive rituals.  
2. Prefer `.venv` interpreter.  
3. Prefer `memory state snapshot` over reading `.agent` dumps.  
4. Full multi-repo `gh` verbatim blocks are **opt-in** (`STRICT_MULTI_REPO=1` or `project_config.git.strict_multi_repo`).  

**Template version:** see `VERSION` (3.3.0+).
