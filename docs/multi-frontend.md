# Multi-Frontend Adapters

Agentix is frontend-agnostic. The loop discipline (roles, handoffs, memory) works with any capable agent UI.

## Supported Frontends

| Frontend | Setup | Notes |
|----------|-------|-------|
| **Grok CLI** | `source .venv/bin/activate` so `GROK_CLI_CHAT_PROXY_BASE_URL` points at `:8110/v1` | Default live path. Host pxpipe is the imager. See [proxy.md](proxy.md). |
| **Blackbox AI CLI** | Official installer, then `--adapter blackbox` | Opt-in subprocess CLI. Wizard default stays **grok**. Not the X11 WM. |
| **Blackbox + VS Code** | `Agent-Init.ps1` or `.sh` | Paste-prompt path. Does not honor the Grok chat-proxy env. pxpipe warp is optional, not auto. |
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

Subprocess CLI via `--adapter blackbox` or `supervisor.adapter: "blackbox"` in `.agent/project_config.json`. Wizard/live default stays **grok**; Blackbox is opt-in.

### Install

Official Unix install, then interactive configure (required):

```bash
curl -fsSL https://blackbox.ai/install.sh | bash
blackbox configure
```

Windows:

```powershell
iex (irm https://blackbox.ai/install.ps1)
```

Then put the binary on PATH and confirm `blackbox --help` is **not** the X11 window manager (must not print Shaleh / `blackbox 0.77` / `-display`).

**Forbidden:** `npm install blackbox-cli` (Ellipse Technology IoT OpenAPI generator, bin `bb`) and `npm install @blackbox/cli` (registry 404 as of 2026-08-25). Canonical install is the curl installer, Node 20+.

`BLACKBOX_API_KEY` is inherited if present. Export-alone is **not** documented as sufficient for the CLI; `blackbox configure` is required (Agent API docs are a different surface).

### Host collision

`/usr/bin/blackbox` on Arch/Debian is often **Blackbox 0.77**, Sean 'Shaleh' Perry's X11 window manager (`-display`, `-rc`). The adapter probes `--help` and refuses it. Put the AI CLI in `~/.local/bin` (searched first).

### Probe

- Unix: `scripts/probe_blackbox.sh` (fail-hard, never npm)
- Windows: `blackbox --help` + PATH; no `.ps1` probe in this release

### Config

Example (`.agent/project_config.example.json`):

```json
"blackbox": {
  "command": "blackbox",
  "prompt_mode": "p",
  "extra_args": []
}
```

`search_paths` is omitted on purpose. Missing or `null` uses code defaults (`~/.local/bin` first; optional `~/.blackbox/bin` / `~/.blackbox-cli-v2` only if a launcher named `blackbox` already exists — we do not exec `node cli.js`). `[]` means PATH only and **disables** the WM-collision fix — do not put `[]` in the shipped example.

Default argv is `-p` (headless; Medium confidence: marketing page + current adapter shape; not listed on commands-reference). Escape hatches: `prompt_mode` `run` (instruction file) or `positional`. Never pass subcommand `p` (`blackbox project`).

```bash
python -m memory.supervisor run --adapter blackbox --max-cycles 1 --no-pr
```

Paste-prompt (VS Code + MiniMax) remains: `Agent-Init.ps1 -OutputFile blackbox_start_prompt.txt`. That is not the subprocess adapter.

## Common Requirements (All Frontends)

- UTF-8 for all files
- Russian human commit messages (no AI mentions)
- Git self-cycle §11 before planning
- Playbooks consult at PLAN start