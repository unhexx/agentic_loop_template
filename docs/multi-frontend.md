# Multi-Frontend Adapters

Agentix is frontend-agnostic. The loop discipline (roles, handoffs, memory) works with any capable agent UI.

## Supported Frontends

| Frontend | Setup | Notes |
|----------|-------|-------|
| **Grok CLI** | `source .venv/bin/activate` so `GROK_CLI_CHAT_PROXY_BASE_URL` points at `:8110/v1` | Default live path. Host pxpipe is the imager. See [proxy.md](proxy.md). |
| **Blackbox + VS Code** | `Agent-Init.ps1` or `.sh` | Does not honor the Grok chat-proxy env. pxpipe warp is optional, not auto. |
| **Cursor** | Copy `prompts/short_orchestrator_prompt.md` as first message | Use Agent mode. Point custom rules to `SYSTEM_PROMPT.md`. Same: no auto warp. |
| **Claude Code** | Same short prompts + `AGENT_ROLES.md` blocks | Append role block per handoff. Temperature per role table in `AGENTIC_LOOP_README.md`. |

## Cursor Adapter

1. Open project in Cursor.
2. Add user rules referencing `DEVELOPMENT_STANDARDS.md` and `HANDOFF_SCHEMA.md`.
3. Start with Orchestrator prompt from `prompts/short_orchestrator_prompt.md`.
4. Each role transition: inject the matching block from `AGENT_ROLES.md`.

## Claude Code Adapter

1. Run platform bootstrap (`Agent-Init.ps1` or `Agent-Init.sh`).
2. First message: content of `first_orchestrator_message.md` or short orchestrator prompt.
3. Require strict JSON handoff output per `HANDOFF_SCHEMA.md` at end of every turn.

## Blackbox Adapter

1. Run `Agent-Init.ps1 -OutputFile blackbox_start_prompt.txt`.
2. Model: highest quality available (MiniMax 2.5 class).
3. Custom instructions from `Agent-Init.md`.

## Common Requirements (All Frontends)

- UTF-8 for all files
- Russian human commit messages (no AI mentions)
- Git self-cycle §11 before planning
- Playbooks consult at PLAN start