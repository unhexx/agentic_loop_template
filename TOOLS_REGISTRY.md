# TOOLS REGISTRY — Verified Tool Descriptions and Best Practices (Template 3.1 self-learning, M2.5)

This document is the single source of truth for tool schemas, parameter names, and best-practice usage patterns. All agents (Orchestrator, Coder, etc.) must consult it before calling tools. The examples are adapted for eeagent (MCP gateway, remote executors, policies, skills).

**Important for Blackbox / non-interactive usage:**
- Always use the exact parameter names from the schema returned by search (do not guess).
- For PowerShell, prefer direct calls or the Invoke-Tool helper if provided by the environment.
- UTF-8 for all handoff and log files.

(Full schemas for read_file, write_file, list_dir, search_replace, run_terminal_command, grep, todo_write, gh MCP tools like create_branch/push_files, memory tools, etc. — same structure as original.)

### `read_file` — Read file contents

| Field       | Type   | Required | Description |
|-------------|--------|----------|-------------|
| `path`      | string | yes      | Relative path from project root |
| `encoding`  | string | no       | Default: utf-8 |
| `lines_from`| int    | no       | Start line (1-based) |
| `lines_to`  | int    | no       | End line |

**Best Practice:**
```json
{
  "tool": "read_file",
  "path": "gateway/app/mcp/server.py",
  "lines_from": 1,
  "lines_to": 120,
  "purpose": "Review current implementation of MCP tool registration and skill loading before extending eeagent tools"
}
```

---

### `write_file` — Write / overwrite a file

| Field     | Type   | Required | Description |
|-----------|--------|----------|-------------|
| `path`    | string | yes      | Relative path from project root |
| `content` | string | yes      | Full file content |
| `encoding`| string | no       | Default: utf-8 |

**Best Practice:**
```json
{
  "tool": "write_file",
  "path": "gateway/app/policy/engine.py",
  "content": "# full file content here with new isolation policy...",
  "purpose": "Add extended policy checks for shell_exec and fs operations"
}
```

---

### `append_file` — Append content to a file

| Field     | Type   | Required | Description |
|-----------|--------|----------|-------------|
| `path`    | string | yes      | Relative path |
| `content` | string | yes      | Text to append |

**Best Practice (for logs):**
```json
{
  "tool": "append_file",
  "path": "PROJECT_CONTEXT.md",
  "content": "\n\n## Cycle 2 — 2026-06\n\n**Lessons learned:**\n- Always run Agent-Init at the beginning of a cycle\n- Self-cycle + gh MCP for github remotes required per multi-repo rules",
  "purpose": "Record self-improvement insights from current cycle"
}
```

---

### `list_dir` — List directory contents

| Field       | Type | Required | Description |
|-------------|------|----------|-------------|
| `path`      | string | yes    | Relative path |
| `recursive` | bool   | no     | Default: false |
| `depth`     | int    | no     | Max recursion depth (default: 1) |

**Best Practice:**
```json
{
  "tool": "list_dir",
  "path": "skills/mcp/skills",
  "recursive": false,
  "depth": 1,
  "purpose": "Inspect agent_execution_skill.py and vision_grounding_skill before changes to executor routing or new MCP skills"
}
```

---

### `search_replace` — Precise search & replace in a file

| Field       | Type   | Required | Description |
|-------------|--------|----------|-------------|
| `path`      | string | yes      | File to modify |
| `search`    | string | yes      | Exact text or regex to find |
| `replace`   | string | yes      | Replacement text |
| `use_regex` | bool   | no       | Default: false |

**Best Practice (preferred over write_file for small changes):**
```json
{
  "tool": "search_replace",
  "path": "gateway/app/policy/engine.py",
  "search": "def is_command_allowed(",
  "replace": "def is_command_allowed(..., tool_name: str, command: str):\n    # policy for shell and fs with isolation tags\n    ...",
  "purpose": "Add missing policy logic for new shell_exec and approval flows"
}
```

---

(Other tools: run_terminal_command, grep, todo_write, use_tool, image_gen, gh MCP tools like grok_com_github__push_files / create_branch with full schemas from search. Best practices updated to eeagent MCP, central, agent, skills, policy, models.)

**Example for git_commit (illustrative of Russian commit rule — the actual message in real commit must be natural Russian human voice):**
```json
{
  "tool": "git_commit",
  "message": "добавил новый MCP инструмент shell.exec с политикой изоляции",
  "add_all": true,
  "purpose": "Зафиксировать расширение инструментов и политик eeagent"
}
```

**Template Version:** 3.1 (self-learning, English, M2.5, eeagent-specific examples, foreign project garbage removed)
