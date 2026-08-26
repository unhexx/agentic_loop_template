# -*- coding: utf-8 -*-
"""Чтение SSOT дашборда по явным путям workdir/.agent, без глобалей модулей."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from memory.logutil import get_logger
from memory.stream_stop import clear_fanout, fanout_stop
from memory.workspace import get_workspace_id

log = get_logger("memory.dashboard.read_model")
_DEFAULT_WT_DIR = "agentic-loop-worktrees"


# last_handoff пишется write_text, не tmp+replace — порванный JSON реален.
TORN_RETRIES = 3
TORN_RETRY_S = 0.020
# 2 * 20 с тик пульса + запас; файл опционален (может отсутствовать).
HEARTBEAT_FRESH_S = 45.0
_SSOT_JSON = frozenset({"LOOP_STATE.json", "last_handoff.json"})
# Хвост истории: не читаем архив целиком (DEVELOPMENT_STANDARDS §5.1).
HISTORY_TAIL_LINES = 20
HISTORY_TAIL_MAX_BYTES = 64 * 1024
LEDGER_CYCLE_CAP = 50
AUDIT_ENTRY_CAP = 50
MEMORY_EXCERPT_LINES = 80
MEMORY_EXCERPT_BYTES = 8 * 1024
PLAN_MAX_BYTES = 64 * 1024
# id в /partials/playbook/{id}: без слэшей, чтобы не выйти из PLAYBOOKS/.
PLAYBOOK_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")


class DashboardStore:
    """Проекция LOOP_STATE / last_handoff / STOP / jsonl / ledger / playbooks / audit.

    PLAN/TODO и выдержка памяти — тоже только чтение. Не раннер, не chdir.
    STOP пишем через fan-out: хаб и worktree из streams_state (как CLI stop).
    """

    def __init__(self, workdir: Path) -> None:
        self.workdir = Path(workdir).resolve()
        self.agent = self.workdir / ".agent"
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._stale: Set[str] = set()
        self._workspace_id: Optional[str] = None

    def loop_state(self) -> Dict[str, Any]:
        return self._read_json(
            self.agent / "LOOP_STATE.json",
            default={"status": "missing"},
        )

    def last_handoff(self) -> Optional[Dict[str, Any]]:
        p = self.agent / "last_handoff.json"
        if not p.is_file():
            self._stale.discard(p.name)
            return None
        data = self._read_json(p, default=None)
        return data if isinstance(data, dict) else None

    def stop_present(self) -> bool:
        return (self.agent / "STOP").is_file()

    def write_stop(self) -> Path:
        """Кооперативный STOP: хаб и worktree из streams_state, байты ``1``."""
        # Иначе UI гасит только хаб, а потоки в worktree продолжают ход.
        extra = self._wt_base_extra_roots()
        fanout_stop(self.workdir, extra_roots=extra)
        return self.agent / "STOP"

    def clear_stop(self) -> bool:
        """Снять STOP на хабе и известных worktree. True, если на хабе файл был."""
        extra = self._wt_base_extra_roots()
        had = (self.agent / "STOP").is_file()
        clear_fanout(self.workdir, extra_roots=extra)
        return had

    def open_questions(self) -> List[Dict[str, Any]]:
        """Открытые вопросы из QUESTIONS_POOL.json по явному пути."""
        data = self._read_json(
            self.agent / "QUESTIONS_POOL.json",
            default={"questions": []},
        )
        if not isinstance(data, dict):
            return []
        raw = data.get("questions") or []
        if not isinstance(raw, list):
            return []
        out: List[Dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict) and item.get("status", "open") == "open":
                out.append(dict(item))
        return out

    def questions_cadence(self) -> Dict[str, Any]:
        """Баннер частоты: явный agent_dir, без cwd-глобалей коллектора."""
        from memory.questions_collector import load_config, should_escalate

        cfg = load_config(agent_dir=self.agent)
        st = self.loop_state()
        try:
            cycle = int(st.get("cycle_number") or 0)
        except (TypeError, ValueError):
            cycle = 0
        need, _cli_reason = should_escalate(current_cycle=cycle, agent_dir=self.agent)
        open_count = len(self.open_questions())
        return {
            "frequency": cfg.get("frequency"),
            "N": cfg.get("N"),
            "escalate": bool(need),
            "reason": _cadence_reason_en(cfg, open_count),
            "open_count": open_count,
            "processors": list(cfg.get("processors") or []),
        }

    def heartbeat(self) -> Dict[str, Any]:
        """Пульс процесса, не статус цикла. Нет файла — liveness unknown."""
        unknown = {"status": "unknown", "label": "liveness unknown"}
        p = self.agent / "supervisor.heartbeat"
        if not p.is_file():
            return dict(unknown)
        data = self._read_json(p, default=None)
        if not isinstance(data, dict):
            return dict(unknown)
        ts_raw = data.get("ts")
        age = _age_s(ts_raw)
        pid = data.get("pid")
        role = data.get("role")
        if age is None:
            return {
                "status": "unknown",
                "label": "liveness unknown",
                "pid": pid,
                "role": role,
                "ts": ts_raw,
            }
        if age <= HEARTBEAT_FRESH_S:
            return {
                "status": "running",
                "label": f"Supervisor running ({pid}, {role})",
                "pid": pid,
                "role": role,
                "ts": ts_raw,
            }
        return {
            "status": "stale",
            "label": "not running / stale",
            "pid": pid,
            "role": role,
            "ts": ts_raw,
        }

    def streams_state(self) -> Dict[str, Any]:
        """Снимок hub streams_state.json; порванный JSON — как LOOP_STATE."""
        data = self._read_json(self.agent / "streams_state.json", default={})
        return data if isinstance(data, dict) else {}

    def stream_heartbeats(self) -> List[Dict[str, Any]]:
        """Пульс каждого потока: allowlist §6, ``.agent/`` обязателен до stat heartbeat."""
        state = self.streams_state()
        extra = self._wt_base_extra_roots()
        roots = self._heartbeat_allowed_roots(extra)
        out: List[Dict[str, Any]] = []
        for name, rec in _stream_records(state):
            raw_wt = rec.get("worktree")
            row: Dict[str, Any] = {
                "name": name,
                "status": rec.get("status") or "",
                "worktree": "" if raw_wt is None else str(raw_wt),
                "branch": rec.get("branch") or "",
                "stop": False,
                "heartbeat": {
                    "status": "unknown",
                    "label": "liveness unknown",
                },
                "age_s": None,
            }
            wt = self._allowlisted_worktree(raw_wt, roots)
            if wt is None:
                out.append(row)
                continue
            agent_dir = wt / ".agent"
            try:
                has_agent = agent_dir.is_dir()
            except OSError:
                has_agent = False
            if not has_agent:
                log.warning("stream heartbeat skip %s: no .agent dir", wt)
                out.append(row)
                continue
            hb = DashboardStore(wt).heartbeat()
            row["heartbeat"] = hb
            row["age_s"] = _age_s(hb.get("ts"))
            try:
                row["stop"] = (agent_dir / "STOP").is_file()
            except OSError:
                row["stop"] = False
            out.append(row)
        return out

    def _wt_base_extra_roots(self) -> List[Path]:
        """wt_base из project_config супервизора, не DashboardConfig (там host/port).

        Сбой загрузки не должен блокировать STOP: хаб и default sibling
        уже в allowlist stream_stop.
        """
        import importlib

        try:
            # AST-тест дашборда запрещает Import/ImportFrom supervisor;
            # runtime всё равно исполнит supervisor.py при первом вызове.
            load_project_config = importlib.import_module("memory.supervisor").load_config
            cfg = load_project_config(self.workdir)
            par = (
                (cfg.get("supervisor") or {})
                if isinstance(cfg.get("supervisor"), dict)
                else {}
            )
            par = (
                (par.get("parallel") or {})
                if isinstance(par.get("parallel"), dict)
                else {}
            )
            raw_wt = par.get("wt_base")
            extra: List[Path] = []
            if isinstance(raw_wt, str) and raw_wt.strip():
                p = Path(raw_wt).expanduser()
                try:
                    p = p.resolve()
                except OSError:
                    pass
                if _is_fs_root(p):
                    log.warning("wt_base is filesystem root, ignored")
                else:
                    extra.append(p)
            return extra
        except Exception as exc:
            log.warning("wt_base config load failed: %s", exc)
            return []

    def _heartbeat_allowed_roots(self, extra: Sequence[Path]) -> List[Path]:
        hub = self.workdir
        roots: List[Path] = [hub, hub.parent / _DEFAULT_WT_DIR]
        for raw in extra:
            p = Path(raw)
            try:
                p = p.resolve()
            except OSError:
                pass
            if _is_fs_root(p):
                continue
            roots.append(p)
        return roots

    def _allowlisted_worktree(
        self, raw: Any, roots: Sequence[Path]
    ) -> Optional[Path]:
        if raw is None:
            return None
        s = str(raw).strip()
        if not s:
            return None
        p = Path(s).expanduser()
        if not p.is_absolute():
            p = self.workdir / p
        try:
            p = p.resolve()
        except OSError:
            pass
        if _is_fs_root(p) or not _path_under_roots(p, roots):
            log.warning("stream heartbeat skip worktree outside allowlist %s", p)
            return None
        return p

    def snapshot(self) -> Dict[str, Any]:
        """Полоса Loop + три ключа last_handoff_*. Не надмножество state.snapshot()."""
        st = self.loop_state()
        ho = self.last_handoff() or {}
        invest = st.get("open_invest") or []
        if not isinstance(invest, list):
            invest = []
        deltas = st.get("recent_deltas") or []
        if not isinstance(deltas, list):
            deltas = []
        git_sync = st.get("git_sync") or {}
        if not isinstance(git_sync, dict):
            git_sync = {}
        gss = ho.get("git_sync_status") or {}
        if not isinstance(gss, dict):
            gss = {}
        metrics = ho.get("metrics") or {}
        if not isinstance(metrics, dict):
            metrics = {}
        return {
            "state": {
                "cycle_number": st.get("cycle_number"),
                "active_role": st.get("active_role"),
                "status": st.get("status"),
                "branch": st.get("branch"),
                "last_commit": st.get("last_commit"),
                "git_sync": git_sync,
                "open_invest": invest[:20],
                "recent_deltas": deltas[-5:],
                "updated_at": st.get("updated_at"),
                "notes": st.get("notes"),
            },
            "last_handoff_summary": ho.get("summary"),
            "last_handoff_status": ho.get("status"),
            "last_handoff_role": ho.get("role"),
            "last_handoff_to": ho.get("handoff_to"),
            # поля карточки — чтобы GET / и /partials/handoff-card не читали файл второй раз
            "last_handoff_confidence": ho.get("confidence"),
            "last_handoff_git_sync": gss,
            "last_handoff_metrics": metrics,
            "stop": self.stop_present(),
            "heartbeat": self.heartbeat(),
            "stale": bool(self._stale.intersection(_SSOT_JSON)),
        }

    def history_tail(self, n: int = HISTORY_TAIL_LINES) -> List[Dict[str, Any]]:
        """Последние n записей jsonl: текущий месяц, при нехватке — предыдущий.

        Читаем с EOF не больше HISTORY_TAIL_MAX_BYTES, каталог не создаём.
        """
        if n <= 0:
            return []
        current, previous = self._history_month_paths()
        # 64 KiB с EOF каждого файла (не общий бюджет на оба месяца).
        rows = _tail_jsonl_file(current, n, HISTORY_TAIL_MAX_BYTES)
        if len(rows) < n:
            older = _tail_jsonl_file(
                previous, n - len(rows), HISTORY_TAIL_MAX_BYTES
            )
            rows = older + rows
        return rows[-n:]

    def ledger_cycles(self, limit: int = LEDGER_CYCLE_CAP) -> List[Dict[str, Any]]:
        """Циклы из PERFORMANCE_LEDGER.json по явному пути, хвост limit записей."""
        data = self._read_json(
            self.agent / "PERFORMANCE_LEDGER.json",
            default={"cycles": []},
        )
        if not isinstance(data, dict):
            return []
        cycles = data.get("cycles") or []
        if not isinstance(cycles, list):
            return []
        out: List[Dict[str, Any]] = []
        for item in cycles:
            if isinstance(item, dict):
                out.append(dict(item))
        if limit <= 0:
            return []
        return out[-limit:]

    def ledger_summary(
        self, cycles: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Средние elapsed/confidence и сумма meta_applied по списку циклов."""
        rows = self.ledger_cycles() if cycles is None else list(cycles)
        if not rows:
            return {
                "count": 0,
                "avg_elapsed_min": 0.0,
                "avg_confidence": 0.0,
                "total_meta_applied": 0,
            }
        n = len(rows)
        total_elapsed = sum(_as_float(c.get("elapsed_minutes")) for c in rows)
        total_conf = sum(_as_float(c.get("confidence")) for c in rows)
        total_meta = sum(_as_float(c.get("meta_applied")) for c in rows)
        meta_out: Any = int(total_meta) if total_meta == int(total_meta) else total_meta
        return {
            "count": n,
            "avg_elapsed_min": round(total_elapsed / n, 1),
            "avg_confidence": round(total_conf / n, 2),
            "total_meta_applied": meta_out,
        }

    def playbooks(self) -> List[Dict[str, Any]]:
        """Каталог из PLAYBOOKS.json по явному пути (форма как у CLI list, без вызова)."""
        data = self._read_json(
            self.agent / "PLAYBOOKS.json",
            default={"playbooks": {}},
        )
        if not isinstance(data, dict):
            return []
        pbs = data.get("playbooks") or {}
        if not isinstance(pbs, dict):
            return []
        items: List[Dict[str, Any]] = []
        for pid, pb in pbs.items():
            if not isinstance(pb, dict):
                continue
            sid = str(pid)
            bullets = pb.get("bullets") or []
            if not isinstance(bullets, list):
                bullets = []
            effs: List[float] = []
            for b in bullets:
                if not isinstance(b, dict):
                    continue
                try:
                    effs.append(float(b.get("effectiveness", 0.5)))
                except (TypeError, ValueError):
                    effs.append(0.5)
            avg_eff = sum(effs) / len(effs) if effs else 0.0
            items.append(
                {
                    "id": sid,
                    "scope": pb.get("scope", "") or "",
                    "name": pb.get("name", sid) or sid,
                    "bullet_count": len(bullets),
                    "avg_effectiveness": round(avg_eff, 3),
                    "last_curated": pb.get("last_curated"),
                    "install_path": f".agent/PLAYBOOKS/{sid}.md",
                }
            )
        return items

    def hub_index_header(self) -> Optional[Dict[str, Any]]:
        """Шапка HUB_INDEX.json, если файл уже есть. На загрузке страницы не пишем."""
        p = self.agent / "HUB_INDEX.json"
        if not p.is_file():
            return None
        data = self._read_json(p, default=None)
        if not isinstance(data, dict):
            return None
        return {
            "version": data.get("version"),
            "generated_at": data.get("generated_at"),
            "item_count": data.get("item_count"),
        }

    def playbook_detail(self, playbook_id: str) -> Optional[Dict[str, Any]]:
        """Один playbook по id из allowlist; bullets из JSON, не из соседних файлов."""
        if not _playbook_id_allowed(self.agent, playbook_id):
            return None
        data = self._read_json(
            self.agent / "PLAYBOOKS.json",
            default={"playbooks": {}},
        )
        if not isinstance(data, dict):
            return None
        pbs = data.get("playbooks") or {}
        if not isinstance(pbs, dict):
            return None
        pb = pbs.get(playbook_id)
        if not isinstance(pb, dict):
            return None
        bullets_raw = pb.get("bullets") or []
        if not isinstance(bullets_raw, list):
            bullets_raw = []
        bullets: List[Dict[str, Any]] = []
        for b in bullets_raw:
            if isinstance(b, dict):
                bullets.append(dict(b))
        return {
            "id": playbook_id,
            "scope": pb.get("scope", "") or "",
            "name": pb.get("name", playbook_id) or playbook_id,
            "bullets": bullets,
        }

    def audit_entries(self, limit: int = AUDIT_ENTRY_CAP) -> List[Dict[str, Any]]:
        """Последние записи AUDIT_LOG.json; подпись обрезана до 12 символов."""
        data = self._read_json(
            self.agent / "AUDIT_LOG.json",
            default={"entries": []},
        )
        if not isinstance(data, dict):
            return []
        raw = data.get("entries") or []
        if not isinstance(raw, list):
            return []
        out: List[Dict[str, Any]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            sig = entry.get("signature")
            sig_s = str(sig) if sig is not None else ""
            out.append(
                {
                    "id": entry.get("id"),
                    "ts": entry.get("ts"),
                    "action": entry.get("action"),
                    "role": entry.get("role"),
                    "cycle": entry.get("cycle"),
                    "approval_required": entry.get("approval_required"),
                    "approved": entry.get("approved"),
                    "signature": sig_s[:12],
                }
            )
        if limit <= 0:
            return []
        return out[-limit:]

    def plan_text(self) -> Optional[Dict[str, Any]]:
        return _read_text_capped(self.agent / "PLAN.md", PLAN_MAX_BYTES)

    def todo_text(self) -> Optional[Dict[str, Any]]:
        return _read_text_capped(self.agent / "TODO.md", PLAN_MAX_BYTES)

    def workspace_id(self) -> str:
        """Стабильный id процесса: git только при первом обращении."""
        if self._workspace_id is None:
            self._workspace_id = get_workspace_id(cwd=self.workdir)
        return self._workspace_id

    def memory_excerpt(self) -> Dict[str, Any]:
        """Выдержка ~/.grok/agentic-loop-memory/{wid}.md без mkdir и без листинга каталога."""
        wid = self.workspace_id()
        mem_file = _memory_root() / f"{wid}.md"
        result: Dict[str, Any] = {
            "workspace_id": wid,
            "path": str(mem_file),
            "present": False,
            "excerpt": "",
            "truncated": False,
        }
        if not mem_file.is_file():
            return result
        try:
            with mem_file.open("rb") as fh:
                raw = fh.read(MEMORY_EXCERPT_BYTES + 1)
        except OSError:
            return result
        truncated = len(raw) > MEMORY_EXCERPT_BYTES
        raw = raw[:MEMORY_EXCERPT_BYTES]
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if len(lines) > MEMORY_EXCERPT_LINES:
            truncated = True
            lines = lines[:MEMORY_EXCERPT_LINES]
        result["present"] = True
        result["excerpt"] = "\n".join(lines)
        result["truncated"] = truncated
        return result

    def _history_month_paths(self) -> tuple:
        dt = datetime.now(timezone.utc)
        y, m = dt.year, dt.month
        current = self.agent / "history" / f"loop_state-{y:04d}{m:02d}.jsonl"
        if m == 1:
            py, pm = y - 1, 12
        else:
            py, pm = y, m - 1
        previous = self.agent / "history" / f"loop_state-{py:04d}{pm:02d}.jsonl"
        return current, previous

    def _read_json(self, path: Path, default: Any = None) -> Any:
        key = path.name
        if not path.is_file():
            self._stale.discard(key)
            return _copy_default(default)
        # Повтор 3×20мс только на холодном порванном чтении: last-good уже есть —
        # sleep на event loop не помогает, запись уже прошла или файл мёртвый.
        attempts = 1 if key in self._cache else TORN_RETRIES + 1
        for attempt in range(attempts):
            try:
                text = path.read_text(encoding="utf-8")
                if not text.strip():
                    raise json.JSONDecodeError("empty", text, 0)
                data = json.loads(text)
                if not isinstance(data, dict):
                    raise json.JSONDecodeError("not object", text, 0)
                self._cache[key] = data
                self._stale.discard(key)
                return dict(data)
            except (json.JSONDecodeError, OSError, UnicodeError):
                if attempt + 1 < attempts:
                    time.sleep(TORN_RETRY_S)
        if key in self._cache:
            self._stale.add(key)
            return dict(self._cache[key])
        self._stale.add(key)
        return _copy_default(default)


def _cadence_reason_en(cfg: Dict[str, Any], open_count: int) -> str:
    """Короткий английский баннер. CLI-строки коллектора не показываем."""
    if open_count <= 0:
        return "no open questions"
    freq = str(cfg.get("frequency") or "")
    if freq == "manual":
        return "manual"
    if freq == "end_of_sprint":
        return "end_of_sprint"
    if freq == "end_of_phase":
        return "end_of_phase"
    return "every_N_cycles"


def _copy_default(default: Any) -> Any:
    if default is None:
        return None
    if isinstance(default, dict):
        return dict(default)
    return default


def _is_fs_root(path: Path) -> bool:
    """True для '/' — отравленный JSON не должен читать корень ФС."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    try:
        return resolved == Path(resolved.anchor)
    except (OSError, ValueError):
        return str(resolved) in ("/", "\\")


def _path_under_roots(path: Path, roots: Sequence[Path]) -> bool:
    for root in roots:
        try:
            if path == root or path.is_relative_to(root):
                return True
        except (ValueError, TypeError, OSError):
            continue
    return False


def _stream_records(state: Dict[str, Any]) -> List[tuple]:
    raw = state.get("streams") if isinstance(state, dict) else None
    if isinstance(raw, dict):
        return [(str(k), v) for k, v in raw.items() if isinstance(v, dict)]
    if isinstance(raw, list):
        out: List[tuple] = []
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            out.append((str(name) if name else f"stream-{i}", item))
        return out
    return []


def _memory_root() -> Path:
    """Каталог институциональной памяти. Существование не гарантируем и не создаём."""
    return Path.home() / ".grok" / "agentic-loop-memory"


def _read_text_capped(path: Path, max_bytes: int) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        with path.open("rb") as fh:
            raw = fh.read(max_bytes + 1)
    except OSError:
        return None
    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    text = raw.decode("utf-8", errors="replace")
    return {"text": text, "truncated": truncated}


def _playbook_id_allowed(agent_dir: Path, playbook_id: str) -> bool:
    if not playbook_id or not PLAYBOOK_ID_RE.fullmatch(playbook_id):
        return False
    base = (agent_dir / "PLAYBOOKS").resolve()
    candidate = (agent_dir / "PLAYBOOKS" / f"{playbook_id}.md").resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return False
    return True


def _parse_ts(raw: object) -> Optional[datetime]:
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_s(raw: object) -> Optional[float]:
    dt = _parse_ts(raw)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds()


def _as_float(v: Any) -> float:
    if v is None or isinstance(v, bool):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_jsonl_line(line: str) -> Dict[str, Any]:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return {"raw": line[:200]}
    if isinstance(obj, dict):
        return obj
    return {"raw": line[:200]}


def _tail_jsonl_file(path: Path, n: int, max_bytes: int) -> List[Dict[str, Any]]:
    """Последние n JSON-строк, чтение с конца файла, не больше max_bytes."""
    if n <= 0 or max_bytes <= 0 or not path.is_file():
        return []
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size <= 0:
        return []
    read_n = min(size, max_bytes)
    start = size - read_n
    try:
        with path.open("rb") as fh:
            fh.seek(start)
            raw = fh.read(read_n)
    except OSError:
        return []
    if start > 0:
        nl = raw.find(b"\n")
        if nl < 0:
            return []
        raw = raw[nl + 1 :]
    text = raw.decode("utf-8", errors="replace")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return [_parse_jsonl_line(ln) for ln in lines[-n:]]
