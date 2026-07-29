# Windows Python / venv

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m memory info
.\.venv\Scripts\python.exe -m pytest -q
```

Never use bare `python` when `.venv` exists.
