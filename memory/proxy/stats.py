# -*- coding: utf-8 -*-
"""
Сводка токенов: pxpipe stats + JSONL проекта + последний отчёт компрессора.

measured_saved_pct остаётся null, пока count_tokens-пробы pxpipe = 0.
Не выдумываем процент экономии.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from memory.proxy.audit import events_path
from memory.proxy.cache import stats as cache_stats
from memory.proxy.config import load_proxy_config, project_root
from memory.proxy.health import probe_gateway, probe_pxpipe


def _pxpipe_raw() -> Optional[Dict[str, Any]]:
    exe = shutil.which("pxpipe")
    if not exe:
        return None
    try:
        r = subprocess.run(
            [exe, "stats", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            return None
        data = json.loads(r.stdout)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _pct(num: Any, den: Any) -> Optional[float]:
    try:
        n = float(num)
        d = float(den)
    except (TypeError, ValueError):
        return None
    if d <= 0:
        return None
    return round(100.0 * n / d, 1)


def summarize_pxpipe(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not raw:
        return {
            "requests": 0,
            "compressed_pct": None,
            "reuse_pct": None,
            "measured_saved_pct": None,
        }
    total = int(raw.get("total") or 0)
    compressed = int(raw.get("compressed") or 0)
    measured_events = int(raw.get("baselineMeasuredEvents") or 0)
    saved = raw.get("savedTokensTotal")
    baseline = raw.get("baselineTokensTotal")
    measured: Optional[float] = None
    if measured_events > 0:
        measured = _pct(saved, baseline)
    # reuse: cacheRead / input, если есть; иначе null — не подставляем 99.1 с хоста вслепую
    reuse = _pct(raw.get("cacheReadTokensTotal"), raw.get("inputTokensTotal"))
    hist = raw.get("systemShaHist")
    if isinstance(hist, list) and hist and total:
        # доля самого частого system sha ≈ reuse системного промпта
        top = hist[0]
        if isinstance(top, (list, tuple)) and len(top) >= 2:
            reuse = _pct(top[1], raw.get("ok2xx") or total)
    return {
        "requests": total,
        "compressed_pct": _pct(compressed, total),
        "reuse_pct": reuse,
        "measured_saved_pct": measured,
        "ok2xx": int(raw.get("ok2xx") or 0),
        "passthrough": int(raw.get("passthrough") or 0),
    }


def _read_jsonl(path: Path, limit: int = 5000) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if i >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    except OSError:
        return []
    return rows


def _file_compress_last(root: Path) -> Optional[Dict[str, Any]]:
    for name in ("compressor_last.json", "context_budget_last.json"):
        p = root / ".agent" / name
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            return {
                "tokens_in": data.get("tokens_in") or data.get("total_tokens"),
                "tokens_out": data.get("tokens_out") or data.get("total_tokens_after_compress"),
            }
    return None


def collect_stats(workdir: Optional[Path] = None) -> Dict[str, Any]:
    root = project_root(workdir)
    cfg = load_proxy_config(root)
    px = probe_pxpipe(cfg)
    gw = probe_gateway(cfg)
    raw = _pxpipe_raw()
    events = _read_jsonl(events_path(root) or (root / ".agent" / "proxy_events.jsonl"))
    fid_toks = [
        int(e["fidelity_tokens"])
        for e in events
        if isinstance(e.get("fidelity_tokens"), (int, float))
    ]
    cache_hits = sum(1 for e in events if e.get("cache_hit"))
    cstat = cache_stats(root)
    return {
        "proxy_mode": cfg.get("mode"),
        "gateway_ok": bool(gw.get("ok")),
        "pxpipe_ok": bool(px.get("ok")),
        "pxpipe": summarize_pxpipe(raw),
        "project_events": len(events),
        "cache_hits": cache_hits or int(cstat.get("hits") or 0),
        "cache_entries": int(cstat.get("entries") or 0),
        "fidelity_tokens_avg": (
            round(sum(fid_toks) / len(fid_toks), 1) if fid_toks else 0
        ),
        "file_compress_last": _file_compress_last(root),
        "slo": {
            "proxy_coverage": "100% non-mock LLM calls when mode=required",
            "file_compress_when_over": "≥25% token cut vs raw tokens_in (compressor report)",
            "pxpipe_eligible_compress": "≥60% when pxpipe present (host compressed field)",
            "system_prompt_reuse": "≥90% when pxpipe present (systemShaHist)",
            "gateway_add_on_p50_ms": 100,
            "measured_raw_token_saved_pct": "unslod until count_tokens probes > 0",
        },
        "notes": (
            "measured_saved_pct is null while pxpipe baselineMeasuredEvents == 0; "
            "do not claim a raw-token savings percentage."
        ),
    }


def collect_for_handoff(workdir: Optional[Path] = None) -> Dict[str, Any]:
    """Узкий объект для optional handoff.proxy_stats."""
    s = collect_stats(workdir)
    px = s.get("pxpipe") or {}
    return {
        "mode": s.get("proxy_mode"),
        "compressed_pct": px.get("compressed_pct"),
        "cache": "hit" if int(s.get("cache_hits") or 0) else "miss",
        "pxpipe_ok": s.get("pxpipe_ok"),
    }
