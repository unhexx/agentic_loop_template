---
name: local-knowledge-ingestion
author: agentix / exception.expert (adapted from eegent)
version: 1.0.0
---

# local-knowledge-ingestion

**Purpose**: Ingest external or multi-source knowledge into structured, queryable local memory so agents avoid re-reading large docs and keep context lean.

## Templates & Patterns

### 1. SQLite local knowledge store (recommended)

```sql
CREATE TABLE IF NOT EXISTS knowledge (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,          -- repo, url, path
  category TEXT,                -- playbook, catalog, api, lesson
  title TEXT,
  content TEXT,                 -- distilled markdown or JSON
  embedding BLOB,               -- optional vector
  provenance TEXT,              -- commit, date, author
  tokens INTEGER,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_knowledge_cat ON knowledge(category);
CREATE INDEX idx_knowledge_src ON knowledge(source);
```

Query via CLI or Python before loading full files:

```bash
python -m memory.knowledge query --category playbook --top 5 --q "git sync"
python -m memory.knowledge ingest-docs --root docs --budget 800
python -m memory.knowledge stats
```

Implemented as `memory/knowledge.py` (SQLite under `.agent/knowledge/knowledge.sqlite`, unique `source+title`, category cap, distill-on-ingest).

### 2. Crawler templates

- **Docs crawler**: walk `docs/`, `*.md`, extract headings + first paragraph → distill → upsert.
- **Code symbols**: tree-sitter or simple AST for public APIs → signature + docstring summary.
- **Catalog style** (from classifier): JSON source-of-truth (`name`, `version`, `items[]` with code/title/symptoms/keywords) → seed playbooks or knowledge table.

### 3. Sovereign mirroring

- Prefer local copy over remote fetch in loop.
- Snapshot external READMEs / specs into `.agent/knowledge/` or workspace memory with provenance.
- Never load multi-MB dumps; always distill first (see PROMPT_COMPRESSION_GUIDE + compressor).

## Workflow

1. Identify knowledge need (Orchestrator cold-start or mid-cycle gap).
2. Choose source (local path, git submodule, URL with cache).
3. Distill (rule-based or LLM summary under budget).
4. Upsert with provenance + token estimate.
5. Expose via `memory query` / playbooks select / tools/select.

## Integration

- Feeds `memory/store.py` categories and playbooks.
- Complements reflective-improvement (lessons become ingested knowledge).
- Use with context_budget: ingest only if total stays under budget.
- Classifier-style catalogs are ideal seeds for domain playbooks.

## Safety

- Respect licenses and private data; strip PII if needed (see classifier pd_cleaner patterns).
- Bound growth: max entries per category, compaction like store.py.
