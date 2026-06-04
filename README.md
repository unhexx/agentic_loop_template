# Agentic Loop Template (MiniMax 2.5)

A complete, self-contained template for running a closed-loop, self-improving multi-role agentic development cycle powered by **MiniMax 2.5**.

- Production-grade: typed, logged, error-handled, tested.
- Non-interactive friendly: Designed to work when AI agents (Blackbox, Continue, etc.) spawn fresh PowerShell processes.
- Self-learning: Built-in memory, context distillation, questions pool, cross-repo sync discipline.
- Aligned with the project's MCP tools and eeagent architecture.
- All model-facing instructions and plans in English (per current cleaning); commits and product code comments in natural Russian (human developer voice, no model names).

## Quick Start (Recommended for Blackbox + MiniMax 2.5)
1. Run `powershell -ExecutionPolicy Bypass -File .\agentic_loop_template\Agent-Init.ps1`
2. Activate venv.
3. Use the generated starter (or first_message.md / short prompt) as system prompt to MiniMax 2.5 via Blackbox.
4. Follow DEVELOPMENT_STANDARDS.md (v3.1 self-learning).

See `Agent-Init.md` for the detailed Blackbox + VSCode launch guide, including recommended Custom Instructions and the first message.

## Adaptation to MiniMax 2.5
This template is tuned for MiniMax 2.5. Recommended settings per role are included in `SYSTEM_PROMPT.md`.
It is designed to be compatible with the tool set described in the host project's MCP / skills docs.

## For heavy data-processing projects
Consider adding dedicated validation or data sanity roles if the project requires heavy parsing or entity resolution.

**Template Version:** 3.1 (self-learning, English, M2.5, eeagent examples, garbage from prior template sources removed)

See DEVELOPMENT_STANDARDS.md, AGENT_ROLES.md, HANDOFF_SCHEMA.md, TOOLS_REGISTRY.md.
