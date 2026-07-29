# Hub Discovery & Install

## Discovery (CLI)

```bash
python -m memory.playbooks list
python -m memory.playbooks discover --query "cross-platform venv" --scope cross-platform
```

Returns JSON with playbook IDs, scopes, bullet counts, and search scores.

## Export Index

```bash
python -m memory.playbooks export --format hub --output .agent/HUB_INDEX.json
```

The index includes:
- `items[]` — catalog metadata (id, scope, name, bullet_count, avg_effectiveness)
- `playbooks{}` — full playbook payloads for offline install

## Install into Consumer Project

1. Copy `agentic_loop_template/` into your repo (or sync via worktree).
2. Export hub index from a golden source repo.
3. Merge selected playbooks:

```python
import json
from pathlib import Path

hub = json.loads(Path(".agent/HUB_INDEX.json").read_text(encoding="utf-8"))
target = json.loads(Path(".agent/PLAYBOOKS.json").read_text(encoding="utf-8"))
for pid, pb in hub["playbooks"].items():
    if pid not in target.get("playbooks", {}):
        target.setdefault("playbooks", {})[pid] = pb
Path(".agent/PLAYBOOKS.json").write_text(
    json.dumps(target, ensure_ascii=False, indent=2), encoding="utf-8"
)
```

4. Verify: `python -m memory.playbooks list`

## Future Hosted Hub

A static JSON feed at `.agent/HUB_INDEX.json` can be published via GitHub Pages or raw CDN. No hosted SaaS is required for v3.3.0 foundation.