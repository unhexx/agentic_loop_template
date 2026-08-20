# -*- coding: utf-8 -*-
"""
Правиловый компрессор рабочего контекста.

Сжимаем по ценности: сначала выкидываем архивы и логи, потом дистиллируем
markdown, в конце режем head+tail под бюджет токенов (rate-distortion).
Без сети и без внешних сервисов. Оценка токенов совпадает с context_budget.

Запуск:
  python -m memory.compressor files --budget 12000 a.md b.md
  python -m memory.compressor distill --text-file note.md --budget 2000
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from memory.context_budget import estimate_tokens as _estimate_tokens


def _tok(text: str) -> int:
    """Пустая строка — ноль токенов (heuristic context_budget даёт минимум 1)."""
    return 0 if not text else _estimate_tokens(text)

# Чем выше — тем дольше держим файл в наборе.
_KEEP_RULES: List[Tuple[re.Pattern[str], int]] = [
    (re.compile(r"(^|[/\\])last_handoff\.json$", re.I), 100),
    (re.compile(r"(^|[/\\])LOOP_STATE\.(json|md)$", re.I), 95),
    (re.compile(r"(^|[/\\])PLAN\.md$", re.I), 90),
    (re.compile(r"(^|[/\\])TODO\.md$", re.I), 88),
    (re.compile(r"(^|[/\\])VERSION$", re.I), 85),
    (re.compile(r"short_[^/\\]*prompt\.md$", re.I), 80),
    (re.compile(r"handoff", re.I), 75),
    (re.compile(r"project_config", re.I), 70),
    (re.compile(r"(LESSONS|DECISIONS)\.md$", re.I), 60),
    (re.compile(r"PLAYBOOKS", re.I), 55),
    (re.compile(r"PERFORMANCE_LEDGER", re.I), 45),
]

# Архивы и логи выкидываем первыми, даже если имя похоже на рабочий набор.
_DROP_FIRST: List[re.Pattern[str]] = [
    re.compile(r"(^|[/\\])history[/\\]", re.I),
    re.compile(r"TRAJECTORIES", re.I),
    re.compile(r"AUDIT_LOG", re.I),
    re.compile(r"\.lock$", re.I),
]

_DEFAULT_PRIORITY = 30
_DROP_PRIORITY = 5
_TRUNC_MARK = "\n\n[... compressed ...]\n\n"
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_MULTI_BLANK = re.compile(r"\n{3,}")
_FENCE = re.compile(r"(```[\w+-]*\n)(.*?)(```)", re.DOTALL)


def file_priority(path: Path) -> int:
    """Приоритет файла: архивы внизу, рабочий набор наверху."""
    s = str(path).replace("\\", "/")
    for rx in _DROP_FIRST:
        if rx.search(s):
            return _DROP_PRIORITY
    best = _DEFAULT_PRIORITY
    for rx, score in _KEEP_RULES:
        if rx.search(s):
            best = max(best, score)
    return best


def distill_text(text: str) -> str:
    """Ужимаем текст без потери заголовков и первых абзацев."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "")
    text = _HTML_COMMENT.sub("", text)
    text = _shrink_fences(text)
    lines = [ln.rstrip() for ln in text.split("\n")]
    text = "\n".join(lines)
    text = _MULTI_BLANK.sub("\n\n", text)
    return text.strip() + ("\n" if text.strip() else "")


def _shrink_fences(text: str) -> str:
    """Длинные блоки кода оставляем головой и хвостом — середина почти не читается в цикле."""

    def _repl(m: re.Match[str]) -> str:
        body = m.group(2)
        lines = body.split("\n")
        if len(lines) <= 40:
            return m.group(0)
        head = "\n".join(lines[:8])
        tail = "\n".join(lines[-4:])
        return f"{m.group(1)}{head}\n# ... {len(lines) - 12} lines omitted ...\n{tail}\n{m.group(3)}"

    return _FENCE.sub(_repl, text)


def truncate_to_budget(text: str, budget_tokens: int) -> str:
    """Режем head+tail, пока не влезем в бюджет. Пустой бюджет — пустой результат."""
    if budget_tokens <= 0:
        return ""
    if _tok(text) <= budget_tokens:
        return text
    # Запас на маркер; держим ~70% головы и 20% хвоста от бюджета.
    mark = _TRUNC_MARK
    mark_tok = _tok(mark)
    remain = max(8, budget_tokens - mark_tok)
    head_tok = max(4, int(remain * 0.78))
    tail_tok = max(4, remain - head_tok)

    # Грубая нарезка по символам: ~4 символа на токен, как в heuristic.
    head_chars = head_tok * 4
    tail_chars = tail_tok * 4
    if len(text) <= head_chars + tail_chars:
        # всё ещё велико по оценке токенов — режем жёстче
        head_chars = max(16, len(text) // 2)
        tail_chars = max(16, len(text) // 5)

    cut = text[:head_chars] + mark + text[-tail_chars:]
    # Если heuristic недооценил — ужимаем голову шагами.
    guard = 0
    while _tok(cut) > budget_tokens and guard < 12:
        head_chars = max(16, int(head_chars * 0.7))
        tail_chars = max(8, int(tail_chars * 0.7))
        cut = text[:head_chars] + mark + text[-tail_chars:]
        guard += 1
    if _tok(cut) > budget_tokens:
        # Последний шанс: только голова.
        chars = max(8, budget_tokens * 3)
        cut = text[:chars]
        while _tok(cut) > budget_tokens and len(cut) > 8:
            cut = cut[: int(len(cut) * 0.7)]
    return cut


def compress_text(text: str, budget_tokens: int) -> Dict[str, Any]:
    """Дистилляция + обрезка одного куска текста."""
    tokens_in = _tok(text)
    distilled = distill_text(text)
    action = "distilled" if distilled != text else "kept"
    out = distilled
    if _tok(out) > budget_tokens:
        out = truncate_to_budget(out, budget_tokens)
        action = "truncated"
    tokens_out = _tok(out)
    return {
        "text": out,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "within_budget": tokens_out <= budget_tokens,
        "action": action,
        "budget_tokens": budget_tokens,
    }


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def compress_files(
    files: Sequence[Path],
    budget_tokens: int,
) -> Dict[str, Any]:
    """
    Сжимаем набор файлов под бюджет.

    Источники не переписываем — только отчёт. Низкий приоритет выкидываем
    целиком, высокий дистиллируем и при необходимости режем.
    """
    items: List[Dict[str, Any]] = []
    for raw in files:
        p = Path(raw)
        text = _read(p) if p.is_file() else ""
        items.append(
            {
                "path": str(p),
                "priority": file_priority(p),
                "exists": p.is_file(),
                "text_in": text,
                "tokens_in": _tok(text),
                "action": "kept",
                "tokens_out": 0,
                "text_out": "",
            }
        )

    tokens_in = sum(int(it["tokens_in"]) for it in items)

    # 1. Дистилляция каждого файла.
    for it in items:
        distilled = distill_text(it["text_in"])
        it["text_out"] = distilled
        it["tokens_out"] = _tok(distilled)
        if distilled != it["text_in"]:
            it["action"] = "distilled"

    def total_out() -> int:
        return sum(int(it["tokens_out"]) for it in items if it["action"] != "dropped")

    # 2. Выкидываем самые дешёвые по приоритету, пока не влезем.
    # Рабочий набор (priority >= 80) не дропаем, пока есть что выкинуть ниже.
    ordered = sorted(items, key=lambda it: (it["priority"], -it["tokens_out"]))
    for it in ordered:
        if total_out() <= budget_tokens:
            break
        if it["priority"] >= 80:
            continue
        if it["action"] == "dropped":
            continue
        it["action"] = "dropped"
        it["tokens_out"] = 0
        it["text_out"] = ""

    # 3. Если всё ещё жирно — режем оставшееся, начиная с низкого приоритета.
    if total_out() > budget_tokens:
        remain = [it for it in items if it["action"] != "dropped"]
        remain.sort(key=lambda it: it["priority"])
        for it in remain:
            if total_out() <= budget_tokens:
                break
            others = total_out() - int(it["tokens_out"])
            room = max(0, budget_tokens - others)
            if room <= 0 and it["priority"] < 80:
                it["action"] = "dropped"
                it["tokens_out"] = 0
                it["text_out"] = ""
                continue
            cut = truncate_to_budget(it["text_out"], max(8, room))
            it["text_out"] = cut
            it["tokens_out"] = _tok(cut)
            it["action"] = "truncated"

    tokens_out = total_out()
    files_out: List[Dict[str, Any]] = []
    for it in items:
        files_out.append(
            {
                "path": it["path"],
                "priority": it["priority"],
                "exists": it["exists"],
                "tokens_in": it["tokens_in"],
                "tokens_out": it["tokens_out"],
                "action": it["action"],
            }
        )

    return {
        "budget_tokens": budget_tokens,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "within_budget": tokens_out <= budget_tokens,
        "over_by": max(0, tokens_out - budget_tokens),
        "files": files_out,
        "dropped": [f["path"] for f in files_out if f["action"] == "dropped"],
        "kept": [f["path"] for f in files_out if f["action"] != "dropped"],
    }


def cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Правиловый компрессор контекста")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_text = sub.add_parser("distill", help="Сжать один текст/файл")
    src = p_text.add_mutually_exclusive_group(required=True)
    src.add_argument("--text-file", type=Path)
    src.add_argument("--stdin", action="store_true")
    p_text.add_argument("--budget", type=int, default=4000)
    p_text.add_argument("--raw", action="store_true", help="Печатать сжатый текст, не JSON")

    p_files = sub.add_parser("files", help="Сжать набор файлов под бюджет")
    p_files.add_argument("paths", nargs="+", type=Path)
    p_files.add_argument("--budget", type=int, default=12000)

    args = parser.parse_args(argv)

    if args.cmd == "distill":
        if args.stdin:
            text = sys.stdin.read()
        else:
            text = _read(args.text_file)
        report = compress_text(text, args.budget)
        if args.raw:
            sys.stdout.write(report["text"])
            return 0 if report["within_budget"] else 1
        print(json.dumps({k: v for k, v in report.items() if k != "text"}, ensure_ascii=False, indent=2))
        return 0 if report["within_budget"] else 1

    report = compress_files(args.paths, args.budget)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["within_budget"] else 1


def main(argv: Optional[List[str]] = None) -> int:
    return cli(argv)


if __name__ == "__main__":
    raise SystemExit(cli())
