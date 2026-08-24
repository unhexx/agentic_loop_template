# Linux Python / venv

```bash
# create if missing
python3 -m venv .venv
./.venv/bin/pip install -U pip
./.venv/bin/pip install -e ".[dev]" \
  || ./.venv/bin/pip install 'jsonschema>=4.18,<5' 'pytest>=8.0,<9'

# always call tools with explicit interpreter
./.venv/bin/python -m memory info
./.venv/bin/python -m pytest -q
```

Never rely on ambient `python` alone when `.venv` exists.
