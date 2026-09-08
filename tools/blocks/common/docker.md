# Docker (operator stack)

Шаблон держит Compose в `deploy/compose.yaml`. Шлюз и pxpipe — процессы хоста.

```bash
python -m memory.stack check
bash scripts/agentix-stack.sh up                 # SearXNG 127.0.0.1:8080
bash scripts/agentix-stack.sh up --research      # + LDR :5000 через шлюз→pxpipe
bash scripts/agentix-stack.sh ps
bash scripts/agentix-stack.sh health
bash scripts/agentix-stack.sh down
python -m memory search --q "langgraph agent" --json
```

Не публиковать порты на 0.0.0.0. Ollama без host port. Живые запросы модели — только через pxpipe.
