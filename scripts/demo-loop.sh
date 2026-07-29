#!/bin/bash
# One-command Agentix demo (P6)
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== Agentix Demo Loop ==="
bash Agent-Init.sh
source .venv/bin/activate

echo "--- Seeding playbooks ---"
python -m memory.playbooks seed --from-standards

echo "--- Plan check ---"
test -f .agent/PLAN.md && test -f TASK_SPECIFICATION.md
echo "PLAN + SPEC: OK"

echo "--- Resume context ---"
python -m memory.resume --json | head -20

echo "--- Eval harness (recent trajectories) ---"
python -m memory.eval_harness --recent 3 2>/dev/null || echo "(no trajectories yet — OK for fresh install)"

echo "--- Hub export ---"
python -m memory.playbooks export --format hub

echo "--- Audit entry ---"
python -m memory.audit_log append \
  --action "demo_loop_complete" \
  --role "demo" \
  --cycle 0 \
  --details '{"script":"demo-loop.sh"}'

echo "=== Demo complete. Start agent with prompts/short_orchestrator_prompt.md ==="