#!/usr/bin/env bash
# Install Agentix into a new project and configure it in one command.
# Usage (from any cwd):
#   bash /path/to/agentic_loop_template/scripts/agentix-init.sh /path/to/new-project \
#     --name my-app --frontend grok --goal "One sentence"
# Flags match `python -m memory init --help`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m memory init --template "$ROOT" "$@"
