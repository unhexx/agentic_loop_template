# Agentic Loop Template

**A production-grade, self-contained template for closed-loop, self-improving multi-role agentic development cycles.**

Powered by frontier LLMs (MiniMax 2.5 / M2.7 via Blackbox, or compatible agents). Optimized for complex, long-running software projects requiring autonomy, consistency, safety, and continuous self-improvement.

---

## 🚀 Value for End Users (Developers & Teams)

This template transforms how you build and maintain sophisticated software systems:

- **10x Productivity Boost for Complex Tasks**: The structured **Orchestrator → Coder → Tester → Debugger → Reviewer** loop allows the LLM to iteratively plan, implement, test, debug, and validate changes until the Reviewer confirms completion. No more one-shot prompts or lost context.

- **Self-Improving & Knowledge Retention**: Every cycle crystallizes lessons, decisions, and questions into persistent memory (`PROJECT_CONTEXT.md`, `SPRINTPLAN.md`, questions pool, meta-harvester). The system gets smarter over time without manual intervention.

- **Reliable & Deterministic Execution**: Strict JSON handoff schemas (`HANDOFF_SCHEMA.md`), typed Python memory layer, robust error handling, and logging. Works reliably in non-interactive environments (Blackbox spawning fresh PowerShell sessions).

- **Real-World Tool Access via MCP**: Extensible tool registry integrates with Model Context Protocol skills for:
  - Shell & file operations
  - Windows GUI automation & vision grounding (e.g., Florence-2 / LocateAnything-style)
  - Remote SSH / fleet management
  - Policy-aware safe execution & sandboxing (Firecracker, Windows JobObject)
  - Analytics, RAG feedback, skill marketplace
  - And custom skills you develop

- **Safety & Isolation First**: Built-in support for strong isolation, policy engines (TOML), audit logging via CommandLog, and human-in-the-loop approvals where needed. Perfect for enterprise or sensitive environments.

- **Multi-Project & Team Ready**: Designed for reuse across multiple repositories (eeagent, and others). Universal template lives here; project-specific configs, envs, and artifacts stay in consumer repos (via `.gitignore` discipline). Multi-repo git sync rules ensure changes to standards/prompts propagate instantly.

- **Best-in-Class Developer Experience**: One-command setup (`Agent-Init.ps1`), detailed guides, Russian natural-language commit messages (human senior dev voice, no AI mentions), prompt compression for long-context models, and alignment with modern agentic architectures.

**Ideal for**: Solo developers or small teams working on large codebases, infrastructure, desktop apps (WinGUI), backend services, or AI/agent platforms who want to leverage frontier models for autonomous, high-quality development velocity while maintaining full control and auditability.

---

## Quick Start (Blackbox + MiniMax 2.5 Recommended)

1. **Prepare Environment** (one-time per project):
   ```powershell
   cd C:\Path\To\Your\Project
   .\agentic_loop_template\Agent-Init.ps1
   ```
   This creates `.venv`, installs deps, sets up PowerShell enhancements, and generates a ready-to-use starter prompt.

2. **Configure Your AI Agent (Blackbox / VS Code)**:
   - Model: MiniMax 2.5 (or highest quality available)
   - Add Custom Instructions from `Agent-Init.md` (includes core rules, commit style, loop discipline).

3. **Launch the Loop**:
   Paste the generated prompt (or content of `first_message.md` / short orchestrator prompt) as your first message to the LLM.
   The agent starts as Orchestrator, creates/updates context files, and begins the cycle.

See `Agent-Init.md` for full Blackbox launch guide, recommended settings, and troubleshooting.

**Important for Consumer Projects**:
- Add `agentic_loop_template/` (and any generated cycle artifacts like `PROJECT_CONTEXT.md`, handoff JSONs, logs) to your project's `.gitignore`.
- Keep your project-specific parameters in separate files: `.env.agentic`, `.env.blackbox`, `config/agentic.env`, etc.
- This template repo is the single source of truth for the universal parts.

---

## How the Agentic Loop Works

```
External Sprint Loop
┌─────────────────────────────────────────────────────────────┐
│  Orchestrator → Coder → Tester → Debugger → Reviewer          │
│       ↑ (if NOT DONE)                                         │
│       └─────────────────────────────── back to Orchestrator   │
│  DONE → Task Complete + Lessons Crystallized                  │
└─────────────────────────────────────────────────────────────┘
```

Inside each role: **PLAN → ACT (max 3 tool calls) → REFLECT → repeat** until handoff.

Reviewer can loop back or approve. All state transferred via strict JSON (`HANDOFF_SCHEMA.md`).

## Key Files & Their Purpose

| File | Purpose |
|------|---------|
| `README.md` | This overview (best practices, value, quick start) |
| `SYSTEM_PROMPT.md` | Master system prompt with {{placeholders}} for project customization. Tuned for MiniMax 2.5 roles and settings. |
| `AGENT_ROLES.md` | Detailed per-role instructions, including prompt compression, git sync discipline (§11), reviewer closure rules. |
| `HANDOFF_SCHEMA.md` | JSON contract for reliable role-to-role and session-to-session state transfer (includes git_sync_status, clarification_questions, etc.). |
| `DEVELOPMENT_STANDARDS.md` | Coding standards, commit rules (Russian human voice), multi-repo discipline, evidence markers, INVEST task principles. |
| `TOOLS_REGISTRY.md` | Catalog of available tools for the local runner / MCP integration. Core + extensible via skills. |
| `TOOLS_INSTRUCTIONS.md` | Usage instructions and examples for tools. |
| `PROJECT_CONTEXT_TEMPLATE.md` | Template for the living project memory/context file. |
| `PROMPT_COMPRESSION_GUIDE.md` | Techniques for handling long context in M2.5/M2.7 (critical for complex projects). |
| `Agent-Init.ps1` / `Agent-Init.md` | One-command robust setup script + detailed usage guide for Blackbox/VSCode. |
| `setup_env_template.ps1` | Environment bootstrap (venv, deps, PowerShell modules). |
| `memory/` | Persistent structured memory: questions collector, meta harvester, schema, store, workspace for cross-cycle learning and sync enforcement. |
| `prompts/` | Optimized starter and short orchestrator prompts for M2.5. |

See also `AGENTIC_LOOP_README.md` for additional eeagent-context details (can be merged or referenced).

## Configuration & Multi-Repo Best Practices

- **Environment Separation**: Never commit secrets or project-specific settings to this template repo or consumer repos' shared history. Use ignored `.env.*` files.
- **Git Discipline (Critical)**: When making changes to the template (prompts, standards, scripts), the Reviewer must output full closure commands covering **all** local clones + remotes of **both** the template repo and all consumer projects (eeagent etc.). Use sync scripts where available. This ensures instant propagation of improvements.
- **GitHub Operations**: After rollout, prefer `gh` CLI verified commands from TOOLS_REGISTRY for any github remote interactions (auth, PRs, etc.). Raw `git` for non-GitHub remotes only.
- **.gitignore in Consumers**: Explicitly ignore the template folder and cycle outputs in product repos to keep histories clean.

## Adaptation to Your Project

1. Copy `agentic_loop_template/` into your repo root (or reference via worktree/sync from dedicated clone).
2. Fill all `{{...}}` placeholders in `SYSTEM_PROMPT.md` (project name, paths, primary goals, tech stack).
3. Create `TASK_SPECIFICATION.md` or equivalent with clear, testable requirements.
4. Run `Agent-Init.ps1` and follow the Blackbox instructions.
5. Customize `TOOLS_REGISTRY.md` / add MCP skills relevant to your domain (GUI, vision, domain-specific APIs, Atlassian integrations, etc.).

For heavy data or enterprise projects: add dedicated validation roles or data sanity checks as needed.

## Technical Highlights

- **Memory Layer**: Python package with schema validation, persistent store, questions pool, meta-optimization. Supports simulation testing and real cross-session continuity.
- **Prompt Engineering**: Role-specific temperatures/top-p, compression guide, short vs full orchestrator prompts.
- **Extensibility**: MCP skills system (see eeagent's `skills/mcp/` for examples: agent execution, vision grounding, Windows GUI, Confluence/Jira ready patterns).
- **Verification**: Smoke tests, GUI integration tests, remote E2E, policy tests included in ecosystem.

## Limitations & Recommendations

- Optimized for Windows + PowerShell + Blackbox + MiniMax-class models. Adaptable to Linux/Mac with minor script changes.
- Max recommended 3-4 full cycles before architecture review to avoid context bloat (use compression & memory).
- For very large monorepos: combine with dedicated sub-agents or LangGraph-style orchestration seeds.

## Version & Evolution

Current unified version incorporates refinements from production usage in eeagent (expanded tools registry & instructions for rich MCP skillset, improved memory module with structured store/workspace, updated prompts and setup scripts, multi-repo sync discipline, environment separation best practices).

Template Version: 3.2+ (unified, production-hardened, MCP/vision/isolation ready, 2026-06)

## Contributing & Governance

- All changes must be backward-compatible or clearly documented.
- Commit messages in natural Russian (human mid/senior developer voice). Never mention AI/LLM/model names.
- Follow `DEVELOPMENT_STANDARDS.md` §11 self-cycle rules and multi-repo discipline.
- Issues/PRs welcome in this repo for template improvements.

## Related Projects

- Main consumer & testbed: https://github.com/unhexx/eegent (full eeagent platform with gateway, local/remote agents, mobile, skills marketplace, strong isolation).
- Roadmap & planning: See eegent's `docs/ROADMAP.md` and `.agent/PLAN.md`.

---

**License**: MIT (or project default)

**Maintained with ❤️ for reliable autonomous development.**

For questions or customization support, open an issue or refer to the detailed docs in the repo.