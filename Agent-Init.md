# Agent Init for Blackbox + MiniMax 2.5 in VSCode

This guide helps you (or your agent) correctly bootstrap the **agentic_loop_template** when working inside **Visual Studio Code + Blackbox AI agent** using **MiniMax 2.5**.

## Why This File Exists

Blackbox (and most VSCode AI agents) have specific behaviors that break normal agentic loops:

- Every command is often executed in a **fresh PowerShell process** (non-interactive).
- `&&`, `||`, `|&` do not work by default.
- The PSReadLine Enter handler can break output capture.
- The agent frequently loses the activated Python virtual environment.
- Profile loading is unreliable.

This document + `Agent-Init.ps1` solve these problems for the `eeagent` project (and any similar Python project using the Agentic Loop Template).

---

## Step 1: One-time Human Setup (Run this yourself)

Open PowerShell in the project root and run:

```powershell
cd X:\LocalRepo\eegent
.\agentic_loop_template\Agent-Init.ps1
```

This script will:
- Create and activate the local `.venv`
- Install all dependencies from `pyproject.toml`
- Install the `posh-bash-chaining` tool into your PowerShell profile (with Blackbox-friendly settings)
- **Force UTF-8 as default encoding** for the current session (prevents mojibake in handoff JSONs on Russian Windows)
- Set helpful environment variables
- Print the exact text you should give to the Blackbox agent

---

## Step 2: Recommended Blackbox Custom Instructions

Before the agent starts, make sure it has the latest `SYSTEM_PROMPT.md` (version 3.1) which contains the **Pre-Flight Checklist**. The agent must verify that all placeholders are filled before beginning any work.

Go to Blackbox settings → Custom Instructions (or System Prompt) and add the following block:

```text
You are working on the eeagent project using the Agentic Loop Template.

CRITICAL RULES:

1. Always start a new task by ensuring the local Python environment is ready:
   powershell -ExecutionPolicy Bypass -File .\agentic_loop_template\Agent-Init.ps1

2. Never run Python commands outside the activated .venv. Always activate it first if needed:
   . .\.venv\Scripts\Activate.ps1

3. For shell commands that use chaining, prefer using the posh-bash-chaining functions or run commands via the bootstrap.

4. Read and strictly follow `DEVELOPMENT_STANDARDS.md` (the single source of truth for all rules in the project):
   - 1. Language of Code, Comments, and Commits (Strict Rule)
   - 2. Code Quality Standards
   - 3. Self-Improvement Discipline
   - 4. Environment and Tooling Rules
   - 5. Handoff and Process Discipline
   - 6. File Encoding (UTF-8 by Default) — Critical for Stability
   - 7. Windows PowerShell Command Hygiene — Critical Anti-Patterns
   - 8. Context Hygiene & Automatic Distillation (v3+)

5. Work iteratively with small, well-tested changes. Run tests frequently.

6. When the task is complex, follow the structure from agentic_loop_template/SYSTEM_PROMPT.md (Orchestrator → Coder → Tester → Debugger → Reviewer).

7. Always read TASK_SPECIFICATION.md (or equivalent) before starting implementation.
```

---

## Step 3: First Message to the Agent (Copy-Paste)

### Best way (recommended)
Run this command — it generates a strong, ready-to-use prompt based on your `TODO.md`:

```powershell
powershell -ExecutionPolicy Bypass -File .\agentic_loop_template\Agent-Init.ps1
```

### Generate a reusable template with placeholders (for new projects)
When you want to copy `agentic_loop_template/` to another project, generate a clean template:

```powershell
powershell -ExecutionPolicy Bypass -File .\agentic_loop_template\Agent-Init.ps1 -GenerateTemplate -OutputFile starter_prompt_template.md
```

Then replace the `{{ PLACEHOLDERS }}` with your project data.

### Manual example (filled version)
```
# Agentic Development Loop — Session Initialization (Template 3.1 self-learning, M2.5)

**Project:** eeagent

## Current Task / Specification
[paste content of TODO.md or TASK_SPECIFICATION.md here]

## MANDATORY FIRST ACTIONS
1. Run: powershell -ExecutionPolicy Bypass -File .\agentic_loop_template\Agent-Init.ps1
2. Activate venv
3. **Immediately read** `agentic_loop_template/DEVELOPMENT_STANDARDS.md` (especially "Windows PowerShell Command Hygiene") — repeated real failures have shown that agents keep making the same Linux-bias + cmd.exe syntax mistakes on Windows.
4. Complete the Pre-Flight Checklist in agentic_loop_template/SYSTEM_PROMPT.md

## REQUIRED READING ORDER (optimized for context efficiency)
1. `agentic_loop_template/DEVELOPMENT_STANDARDS.md` (v3.1 self-learning) — **read first** (the "constitution").
2. `agentic_loop_template/SYSTEM_PROMPT.md`
3. `agentic_loop_template/AGENT_ROLES.md` (now uses short micro-prompts)
4. Current specification file (`TODO.md` / `TASK_SPECIFICATION.md`)
5. `PROJECT_CONTEXT.md` and `SPRINTPLAN.md` (current working memory)

Read other files (HANDOFF_SCHEMA, TOOLS_REGISTRY, detailed examples, etc.) **on demand** only when needed.

Start as ORCHESTRATOR (temperature 0.0). Use PLAN → ACT → REFLECT. All git commits in natural Russian, human developer voice.
```

---

## Recommended VSCode + Blackbox Settings

- **Default Shell**: PowerShell (preferably PowerShell 7 if available)
- **Execution Policy**: Make sure it allows running local scripts:
  ```powershell
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
  ```
- **Terminal**: Use the integrated terminal (not external).
- **Model**: MiniMax 2.5 (or the closest available high-quality model)
- **Temperature**: 0.0 – 0.2 for planning and review roles (let the agent manage this via instructions)

---

## Environment Variables That Help Blackbox

The `Agent-Init.ps1` sets these variables. You can also set them manually:

```powershell
$env:POSH_BASH_CHAINING_NONINTERACTIVE = "1"
$env:BLACKBOX_AGENT_MODE = "1"
```

These tell the tools to behave safely when output is being captured by an agent.

---

## Common Problems & Solutions

(Continuing with typical troubleshooting for non-interactive PowerShell, venv activation, UTF-8, etc. — the full original troubleshooting section applies with updated paths and eeagent references. All instructions in this file are now in English per project cleaning for MiniMax M2.5.)

See the generated prompt and DEVELOPMENT_STANDARDS for full details on self-learning updates (memory snapshot, distillation, cross-repo sync).

**Template Version:** 3.1 (self-learning, English instructions, adapted for MiniMax M2.5)
