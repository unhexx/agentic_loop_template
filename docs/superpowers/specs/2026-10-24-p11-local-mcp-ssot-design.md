# P11 — Local MCP SSOT in agentic_loop_template

| Field | Value |
|-------|--------|
| **Status** | Implemented (v1 port from agentix-hub) |
| **Date** | 2026-10-24 |
| **Baseline** | Template 3.13.0 |
| **Source** | `/home/unhex/_PROJECT/agentix-hub/memory/local_mcp` + design 2026-10-11 |

## Goal

Ship `python -m memory.local_mcp` / `agentix-local-mcp` in the template SSOT so products stop forking hub `memory/`.

## v1 delivered

- Full `memory/local_mcp/` tree (19 tools + act/self_hosted/jenkins backends on disk)
- Minimal `memory/integrations/{config,state,http,slack}` for notify/config gates
- Packaging: `agentix-local-mcp`, `local_mcp/schemas/*.json`
- Template parity: knowledge `readonly`, state snapshot non-mutating, logutil `LocalRedactFilter`
- Hermetic tests: `memory/test_local_mcp.py` core slice (jenkins/self_hosted/webhook deselected in CI default)

## Non-goals (v1.1+)

- Live Jenkins/Bitbucket/act in template CI
- Full tracker suite (jira/linear/service) from hub
- Replacing `tools/select.py` Markdown recipes

## Operator

```bash
python -m memory.local_mcp list-tools
python -m memory.local_mcp serve
agentix-doctor --strict
```
