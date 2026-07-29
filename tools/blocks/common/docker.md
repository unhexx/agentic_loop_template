# Docker (when project uses compose)

```bash
docker compose ps
docker compose up -d --build
docker compose logs --tail 100
```

Prefer compose over ad-hoc `python -m uvicorn` when STANDARDS say Docker-first.
