# P9 Operator Compose Stack Implementation Plan

> **For agentic workers:** implement task-by-task on `feature/p9-operator-stack`. Do not merge to `main`.

**Goal:** Containerize SearXNG + optional Local Deep Research (LangGraph) + optional Ollama in the Agentix template, keep pxpipe on the live LLM path, ship v3.13.0.

**Architecture:** Compose profiles under `deploy/`. Host gateway `:8110` fronts host pxpipe `:8100`. LDR talks to the gateway via `host.docker.internal`. SearXNG JSON is the agent search backend.

**Tech Stack:** Docker Compose v2, SearXNG, localdeepresearch image, stdlib Python, pytest.

---

### Task 1: Contract tests

- Create: `memory/test_stack.py`, `memory/test_search.py`

Hermetic assertions on compose bind, profiles, JSON format, extra_hosts, pxpipe URL, no Ollama host port.

### Task 2: Compose + settings + CLI

- Create: `deploy/compose.yaml`, `deploy/compose.env.example`, `deploy/searxng/settings.yml`, `memory/stack.py`, `memory/search.py`, `scripts/agentix-stack.sh`

### Task 3: Docs, roadmap, 3.13.0

- Modify: `ROADMAP.md`, `SPRINTPLAN.md`, `PROJECT_CONTEXT.md`, `TASK_SPECIFICATION.md`, `CHANGELOG.md`, `VERSION`, README pair, `docs/architecture.md`, `docs/README.md`, docker tool block.

### Task 4: pytest + release commit on the feature branch
