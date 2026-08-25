# -*- coding: utf-8 -*-
"""
Оценка токенов для cold-start / next_input_files.

Порядок: encoding= → AGENTIX_TOKEN_ENCODING → модель (model= / AGENTIX_TOKEN_MODEL / grok)
→ cl100k_base. gpt-4o/o1/o3 → o200k_base. Нет tiktoken — max(1, len//4).

Usage:
  python -m memory.context_budget check --files a.md b.md --budget 12000
  python -m memory.context_budget check --files a.md b.md --budget 12000 --compress
  python -m memory.context_budget cold-start --budget 16000 --compress --model grok
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from memory.logutil import get_logger

log = get_logger("memory.context_budget")

Estimator = Literal["tiktoken", "chars_div_4"]


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    estimator: Estimator
    encoding: Optional[str]
    model: Optional[str]


# Длинный префикс первым. Для не-OpenAI чата — cl100k как текущая аппроксимация.
_MODEL_PREFIXES: tuple[tuple[str, str], ...] = (
    ("gpt-4o", "o200k_base"),
    ("gpt-4", "cl100k_base"),
    ("gpt-3.5", "cl100k_base"),
    ("o1", "o200k_base"),
    ("o3", "o200k_base"),
    ("grok", "cl100k_base"),
    ("blackbox", "cl100k_base"),
    ("cursor", "cl100k_base"),
    ("claude", "cl100k_base"),
    ("gemini", "cl100k_base"),
)
_DEFAULT_MODEL = "grok"
_DEFAULT_ENCODING = "cl100k_base"

_encoders: dict[str, Any] = {}
_tiktoken_missing = False
_logged_fallback: set[str] = set()


def _reset_encoder_cache() -> None:
    """Сброс кэша энкодера — только для тестов."""
    global _tiktoken_missing
    _encoders.clear()
    _logged_fallback.clear()
    _tiktoken_missing = False


def encoding_for_model(model: str) -> str:
    key = (model or "").strip().lower()
    for prefix, enc in _MODEL_PREFIXES:
        if key.startswith(prefix):
            return enc
    return _DEFAULT_ENCODING


def _fallback_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _load_encoder(name: str) -> Any:
    import tiktoken  # type: ignore

    return tiktoken.get_encoding(name)


def _encoder(name: str) -> Any | None:
    global _tiktoken_missing
    if _tiktoken_missing:
        return None
    cached = _encoders.get(name)
    if cached is not None:
        return cached
    try:
        enc = _load_encoder(name)
    except ImportError:
        _tiktoken_missing = True
        if name not in _logged_fallback:
            log.debug("tiktoken missing; chars/4 fallback")
            _logged_fallback.add(name)
        return None
    except Exception as exc:
        if name not in _logged_fallback:
            log.debug("tiktoken encoding %s unavailable: %s", name, exc)
            _logged_fallback.add(name)
        return None
    _encoders[name] = enc
    return enc


def resolve_encoding(
    *,
    model: Optional[str] = None,
    encoding: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Вернуть (имя_кодировки, модель). Без чтения project_config."""
    enc = (encoding or os.environ.get("AGENTIX_TOKEN_ENCODING") or "").strip() or None
    mdl = (model or os.environ.get("AGENTIX_TOKEN_MODEL") or "").strip() or None
    if enc:
        return enc, mdl or _DEFAULT_MODEL
    if not mdl:
        mdl = _DEFAULT_MODEL
    return encoding_for_model(mdl), mdl


def describe_estimate(
    text: str,
    *,
    model: Optional[str] = None,
    encoding: Optional[str] = None,
) -> TokenEstimate:
    enc_name, mdl = resolve_encoding(model=model, encoding=encoding)
    codec = _encoder(enc_name)
    if codec is not None:
        try:
            n = len(codec.encode(text))
            return TokenEstimate(
                tokens=n, estimator="tiktoken", encoding=enc_name, model=mdl
            )
        except Exception as exc:
            if enc_name not in _logged_fallback:
                log.debug("tiktoken encode failed for %s: %s", enc_name, exc)
                _logged_fallback.add(enc_name)
    return TokenEstimate(
        tokens=_fallback_tokens(text),
        estimator="chars_div_4",
        encoding=None,
        model=mdl,
    )


def estimate_tokens(
    text: str,
    *,
    model: Optional[str] = None,
    encoding: Optional[str] = None,
) -> int:
    return describe_estimate(text, model=model, encoding=encoding).tokens


def file_tokens(
    path: Path,
    max_read: int = 2_000_000,
    *,
    model: Optional[str] = None,
    encoding: Optional[str] = None,
) -> Dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False, "bytes": 0, "tokens": 0}
    data = path.read_bytes()[:max_read]
    text = data.decode("utf-8", errors="replace")
    return {
        "path": str(path),
        "exists": True,
        "bytes": path.stat().st_size,
        "tokens": estimate_tokens(text, model=model, encoding=encoding),
        "truncated_read": path.stat().st_size > max_read,
    }


def check_files(
    files: List[Path],
    budget: int,
    compress: bool = False,
    *,
    model: Optional[str] = None,
    encoding: Optional[str] = None,
) -> Dict[str, Any]:
    rows = [file_tokens(p, model=model, encoding=encoding) for p in files]
    total = sum(int(r["tokens"]) for r in rows)
    meta = describe_estimate("x", model=model, encoding=encoding)
    report: Dict[str, Any] = {
        "budget_tokens": budget,
        "total_tokens": total,
        "within_budget": total <= budget,
        "estimator": meta.estimator,
        "encoding": meta.encoding,
        "model": meta.model,
        "files": rows,
        "over_by": max(0, total - budget),
    }
    if compress and not report["within_budget"]:
        # Ленивый импорт: compressor тянет estimate_tokens отсюда.
        from memory.compressor import compress_files

        crep = compress_files(files, budget)
        report["compression"] = crep
        report["total_tokens_after_compress"] = int(crep["tokens_out"])
        report["within_budget"] = bool(crep["within_budget"])
        report["over_by"] = int(crep.get("over_by") or 0)
    return report


def cold_start_default_files(root: Path | None = None) -> List[Path]:
    root = root or Path.cwd()
    candidates = [
        root / "SYSTEM_PROMPT.md",
        root / "prompts" / "short_orchestrator_prompt.md",
        root / ".agent" / "LOOP_STATE.json",
        root / "VERSION",
    ]
    return [p for p in candidates if p.exists()]


def cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Context budget checker")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check")
    p_check.add_argument("--files", nargs="+", type=Path, required=True)
    p_check.add_argument("--budget", type=int, default=12000)
    p_check.add_argument("--strict", action="store_true", help="Exit 1 if over budget")
    p_check.add_argument(
        "--compress",
        action="store_true",
        help="If over budget, run rule-based compressor (does not rewrite sources)",
    )

    p_cold = sub.add_parser("cold-start")
    p_cold.add_argument("--budget", type=int, default=16000)
    p_cold.add_argument("--strict", action="store_true")
    p_cold.add_argument("--root", type=Path, default=None)
    p_cold.add_argument("--compress", action="store_true")

    for p in (p_check, p_cold):
        p.add_argument("--model", default=None)
        p.add_argument("--encoding", default=None)

    args = parser.parse_args(argv)

    do_compress = bool(getattr(args, "compress", False))
    model = getattr(args, "model", None)
    encoding = getattr(args, "encoding", None)
    if args.cmd == "check":
        report = check_files(
            args.files,
            args.budget,
            compress=do_compress,
            model=model,
            encoding=encoding,
        )
    else:
        report = check_files(
            cold_start_default_files(args.root),
            args.budget,
            compress=do_compress,
            model=model,
            encoding=encoding,
        )
        report["profile"] = "cold-start"

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and not report["within_budget"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli())
