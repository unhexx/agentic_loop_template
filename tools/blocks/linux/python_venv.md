# Linux Python / venv

```bash
# create if missing
python3 -m venv .venv
./.venv/bin/pip install -U pip
# if pyproject exists:
./.venv/bin/pip install -e . 2>/dev/null || true
./.venv/bin/pip install pytest jsonschema 2>/dev/null || true

# always call tools with explicit interpreter
./.venv/bin/python -m memory info
./.venv/bin/python -m pytest -q
```

Never rely on ambient `python` alone when `.venv` exists.
