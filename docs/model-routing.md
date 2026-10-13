# Model Routing

## Default policy

The template uses role-based routing for the five-stage loop:

| Role | Provider / model ID | Purpose |
|---|---|---|
| Orchestrator | `anthropic/claude-opus-4-6` | Planning, state continuity, and tool-chain decisions |
| Coder | `anthropic/claude-sonnet-4-6` | Normal implementation and focused tests |
| Tester | `anthropic/claude-sonnet-4-6` | Test design, edge cases, and evidence |
| Debugger | `anthropic/claude-sonnet-4-6` | Root-cause debugging |
| Reviewer | `anthropic/claude-opus-4-6` | Final gate, DONE decision, and lesson harvest |

The Coder fallback is `google/gemini-3.7-flash`, and is restricted to mechanical or scaffolding work. If the same root cause remains unresolved after two Debugger rounds, use `anthropic/claude-opus-4-6` for the next Debugger handoff.

The policy is stored in `.agent/project_config.json` and copied to new projects from `.agent/project_config.example.json`. Role prompts must read this block before acting.

## Fact-check and limits

This is a routing recommendation, not a benchmark claim. Quality, latency, context limits, quota, and tool behavior depend on the frontend, account, region, prompt, and enabled tools. The template does not claim that Opus is always better than Sonnet or that Gemini is unsuitable for every task.

As of 2026-09-25, the official Anthropic model catalog lists Claude Opus 4.6 and Claude Sonnet 4.6 as available legacy models, with adaptive thinking supported on the 4.6 generation. The canonical IDs used here are `claude-opus-4-6` and `claude-sonnet-4-6`. Antigravity may expose different display names or a subset of provider IDs; availability must be checked in that frontend before starting a cycle.

The official Google catalog lists `gemini-3.7-flash` as a stable model and `gemini-3.1-pro-preview` as a preview model. `gemini-3.1-pro-low` is not used as a default ID because it is not an official Google API model ID in the catalog checked on that date. The `-high`, `-medium`, and `-low` suffixes used by this repository's optional Antigravity shim are frontend routing hints, not general Google API model IDs.

## Operational rules

1. Read `model_routing.roles[role]` before every handoff.
2. Verify the model is available in the selected frontend; report `model_unavailable` instead of silently downgrading Orchestrator or Reviewer.
3. Record the actual provider and model in the handoff `performance` or `context_updates` field when the frontend exposes it.
4. Re-evaluate this policy when a model is retired, a frontend changes its catalog, or cycle metrics show a role-specific regression.

The model catalog sources used for this review are [Anthropic's model overview](https://platform.claude.com/docs/en/about-claude/models/overview) and [Google's Gemini model catalog](https://ai.google.dev/gemini-api/docs/models).