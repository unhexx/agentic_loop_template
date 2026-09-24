# xAI tools — local client-side vs server-side

Источник: https://docs.x.ai/developers/tools/advanced-usage#mixing-server-side-and-client-side-tools

Server-side tools xAI выполняет сама. Client-side tools останавливают цикл: агент вызывает локальный сервис и возвращает результат в следующий ход.

Правило шаблона: **всё, что можно сделать локально и без стороннего SaaS — делается локально.**

```bash
python tools/select.py --intent xai
```

## Карта

| xAI tool | Где исполняется | Репозиторий |
|----------|-----------------|-------------|
| `code_execution` | локально | https://github.com/unhexx/tool-code-execution |
| `collections_search` / `file_search` | локально (FTS5) | https://github.com/unhexx/tool-collections-search |
| `browse_page` / `open_page` / `open_page_with_find` | локально (`file://` всегда; http опционально) | https://github.com/unhexx/tool-browse-page |
| `web_search` / `search_images` | локальный корпус; опционально self-hosted SearXNG | https://github.com/unhexx/tool-web-search |
| `view_image` | локально (Pillow / заголовок файла) | https://github.com/unhexx/tool-view-image |
| `mcp_call` | локальный stdio MCP | https://github.com/unhexx/tool-mcp-bridge |
| `x_keyword_search` `x_semantic_search` `x_user_search` `x_thread_fetch` `view_x_video` | **только server-side xAI** | https://github.com/unhexx/tool-x-search (фасад/fixtures) |
| image generation | server-side xAI или локальная Ollama (профиль `ollama`) | не дублировать облако |

Операторский SearXNG шаблона (`deploy/compose.yaml`, `:8080`) — допустимый self-hosted backend для `tool-web-search` (`SEARXNG_URL=http://127.0.0.1:8080`). Это не xAI `web_search`. Второй инстанс не поднимать, если этот уже жив.

## SearXNG

SearXNG — самохостный метапоисковик: принимает запрос, опрашивает настроенные публичные движки и отдаёт агрегированный JSON без трекинга пользователя. В этом контуре он нужен **только** живому `web_search` / `search_images`.

| Режим | Что поднять | Внешние движки |
|-------|-------------|----------------|
| CI / корпус / свои файлы | только `tool-web-search` (`TOOL_OFFLINE=1`) | нет |
| Живой поиск, SearXNG шаблона уже есть | `SEARXNG_URL=http://127.0.0.1:8080`, без профиля `searxng` | да, через уже поднятый инстанс |
| Живой поиск без шаблона | в `tool-web-search`: `docker compose --profile searxng up` | да |

Не ставить SearXNG «на всякий случай» и не дублировать `:8080`.

## Документы в каждом tool-репозитории

У каждого Tool на `main`: `docs/DESIGN.md`, `docs/AGENT_TASKS.md`, `AGENTS.md`. Локальный агент читает дизайн, берёт один открытый пункт из очереди, гоняет `pytest`, обновляет статус в том же цикле.

## Поднять локальные Tools

Каждый Tool — отдельный репозиторий с собственным `docker-compose.yml` (loopback, `cap_drop: ALL`, `no-new-privileges`, read-only root).

```bash
# соседние каталоги относительно продукта
for repo in tool-code-execution tool-collections-search tool-browse-page \
            tool-web-search tool-view-image tool-mcp-bridge tool-x-search; do
  git clone "https://github.com/unhexx/${repo}.git"
  (cd "$repo" && docker compose up -d --build)
done

# либо оверлей шаблона, если репозитории склонированы рядом
docker compose -f deploy/xai-local-tools.compose.yaml up -d --build
```

Проверка без Docker (stdlib + pytest):

```bash
cd tool-code-execution && PYTHONPATH=src python -m pytest -q
```

## Смешивание в запросе к модели

```python
tools = [
    # server-side — только то, что нельзя сделать локально
    {"type": "x_search"},
    # client-side — локальные сервисы
    {**code_execution_schema},      # GET http://127.0.0.1:8091/schema
    {**collections_search_schema},  # :8092
    {**browse_page_schema},         # :8093
    {**web_search_schema},          # :8094
    {**view_image_schema},          # :8095
    {**mcp_call_schema},            # :8096
]
```

Когда в ответе `function_call` / `get_tool_call_type(...) == "client_side_tool"`:

```bash
curl -sS -H "Authorization: Bearer ${TOOL_TOKEN:-local}" \
  -H "Content-Type: application/json" \
  -d '{"arguments":{"code":"print(2+40)"}}' \
  http://127.0.0.1:8091/v1/invoke
```

Результат добавить в следующий ход. Не вызывать тот же Tool и через xAI server-side, и через локальный сервис в одном цикле.

## Порты (loopback)

| Сервис | Порт |
|--------|------|
| tool-code-execution | 8091 |
| tool-collections-search | 8092 |
| tool-browse-page | 8093 |
| tool-web-search | 8094 |
| tool-view-image | 8095 |
| tool-mcp-bridge | 8096 |
| tool-x-search | 8097 |
| SearXNG (шаблон) | 8080 |

Не публиковать `0.0.0.0`. `TOOL_TOKEN` сменить до любого прокси.
