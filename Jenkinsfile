// Mirrors .github/workflows/agentix-loop.yml for Bitbucket Jenkins
// (Multibranch / Bitbucket Branch Source). Script Path: Jenkinsfile.
// Linux or macOS agent with bash and Python 3.10+. Prefer 3.12.
pipeline {
    agent any
    options { timestamps() }
    stages {
        stage('Harness') {
            steps {
                sh '''#!/bin/bash
set -euo pipefail
if command -v python3.12 >/dev/null 2>&1; then
  PY=python3.12
else
  PY=python3
fi
"$PY" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
rm -rf .ci-venv
"$PY" -m venv .ci-venv
# shellcheck disable=SC1091
. .ci-venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev,dashboard]"
'''
                sh '''#!/bin/bash
set -euo pipefail
# shellcheck disable=SC1091
. .ci-venv/bin/activate
# pytest memory/ does not prove G1: it puts the repo root on sys.path.
cd /tmp
env -u PYTHONPATH python -c "import memory, memory.supervisor, memory.validate_handoff; import importlib.metadata as m; print(m.version('agentix'))"
'''
                sh '''#!/bin/bash
set -euo pipefail
# shellcheck disable=SC1091
. .ci-venv/bin/activate
python -m pytest -q memory/
'''
                sh '''#!/bin/bash
set -euo pipefail
# shellcheck disable=SC1091
. .ci-venv/bin/activate
python -m pytest -q memory/test_supervisor_mock_cycle.py
'''
                sh '''#!/bin/bash
set -euo pipefail
# shellcheck disable=SC1091
. .ci-venv/bin/activate
python -c "import httpx, fastapi"
python -m pytest -q memory/test_dashboard_security.py memory/test_dashboard_ws.py
'''
                // Init without --wizard: proxy health is best-effort. Do not set AGENTIX_PROXY=0.
                sh '''#!/bin/bash
set -euo pipefail
bash Agent-Init.sh
'''
                sh '''#!/bin/bash
set -euo pipefail
# shellcheck disable=SC1091
. .venv/bin/activate
python -m memory.playbooks seed --from-standards
test -f .agent/PLAN.md
test -f TASK_SPECIFICATION.md
python -m memory.playbooks export --format hub
GOAL="${BRANCH_NAME:-${GIT_BRANCH:-jenkins}}"
python -m memory.audit_log append \
  --action "jenkins_loop_trigger" \
  --role "ci" \
  --cycle 0 \
  --details "{\"goal\":\"${GOAL}\"}"
'''
            }
        }
        stage('Stdlib collect') {
            steps {
                sh '''#!/bin/bash
set -euo pipefail
if command -v python3.12 >/dev/null 2>&1; then
  PY=python3.12
else
  PY=python3
fi
rm -rf .stdlib-venv
"$PY" -m venv .stdlib-venv
# shellcheck disable=SC1091
. .stdlib-venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
python -m pytest --collect-only -q memory/test_supervisor_fsm.py
'''
            }
        }
    }
}
