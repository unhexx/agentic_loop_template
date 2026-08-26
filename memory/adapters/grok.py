# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from memory.proxy.policy import ProxyNotReady, assert_ready
from memory.stream_context import apply_stream_env
from memory.validate_handoff import validate_handoff

from .persist import persist_role_handoff

log = logging.getLogger("memory.adapters")


class HandoffExtractError(ValueError):
    """Нет JSON или ни один кандидат не проходит validate_handoff."""


def _strict_done_for(candidate: Dict[str, Any]) -> bool:
    return (candidate.get("status") or "").upper() == "DONE"


def extract_json_candidates(text: str) -> List[Dict[str, Any]]:
    """Все dict, которые raw_decode принял, в порядке появления.

    Greedy regex — только если ни один dict не декодирован.
    """
    if not text:
        return []
    decoder = json.JSONDecoder()
    found: List[Dict[str, Any]] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "{":
            try:
                obj, end = decoder.raw_decode(text, i)
                if isinstance(obj, dict):
                    found.append(obj)
                i = end
                continue
            except json.JSONDecodeError:
                pass
        i += 1
    if found:
        return found
    matches = list(re.finditer(r"\{[\s\S]*\}", text))
    if not matches:
        return []
    try:
        obj = json.loads(matches[-1].group(0))
    except json.JSONDecodeError as exc:
        raise HandoffExtractError(
            "Нет JSON или ни один кандидат не проходит validate_handoff."
        ) from exc
    if isinstance(obj, dict):
        return [obj]
    return []


def extract_json_object(text: str) -> Dict[str, Any]:
    """Последний dict (обратная совместимость тестов picks_last)."""
    if not text:
        raise ValueError("no JSON object in adapter output")
    candidates = extract_json_candidates(text)
    if not candidates:
        raise ValueError("no JSON object in adapter output")
    return candidates[-1]


def extract_handoff(text: str) -> Dict[str, Any]:
    """Последний кандидат, который persist примет.

    strict_done=(status==DONE) на каждом кандидате. Без параметра strict_done —
    одно правило с persist_role_handoff. Если валидных нет — HandoffExtractError
    с errors последнего кандидата.
    """
    candidates = extract_json_candidates(text)
    last_valid: Optional[Dict[str, Any]] = None
    last_errors: List[str] = []
    rejected = 0
    for cand in candidates:
        if log.isEnabledFor(logging.DEBUG):
            keys = ",".join(str(k) for k in cand.keys())
            log.debug("extract candidate keys: %s", keys[:200])
        ok, errors = validate_handoff(cand, strict_done=_strict_done_for(cand))
        if ok:
            last_valid = cand
        else:
            rejected += 1
            last_errors = errors
    if last_valid is not None:
        if rejected:
            log.warning("extract_handoff rejected %s candidates", rejected)
        return last_valid
    log.warning("extract_handoff rejected %s candidates", rejected)
    msg = (
        "; ".join(last_errors)
        if last_errors
        else "Нет JSON или ни один кандидат не проходит validate_handoff."
    )
    raise HandoffExtractError(msg)


def apply_proxy_env(env: Dict[str, str], workdir: Path) -> Dict[str, str]:
    """Маршрут Grok CLI: required — локальный хоп; off — без Agentix URL."""
    from memory.proxy.config import (
        DEFAULT_GATEWAY_BASE,
        DEFAULT_INSTALL_CHAT_PROXY,
        effective_mode,
        load_proxy_config,
    )

    pcfg = load_proxy_config(workdir)
    mode = effective_mode(pcfg)
    gateway = str(pcfg.get("gateway_base") or DEFAULT_GATEWAY_BASE)
    chat = str(pcfg.get("chat_proxy") or DEFAULT_INSTALL_CHAT_PROXY)
    if mode == "off":
        env.pop("GROK_CLI_CHAT_PROXY_BASE_URL", None)
        env.pop("AGENTIX_GATEWAY_URL", None)
        return env
    if mode == "required":
        env["GROK_CLI_CHAT_PROXY_BASE_URL"] = chat
        env["AGENTIX_GATEWAY_URL"] = gateway
        return env
    env.setdefault("GROK_CLI_CHAT_PROXY_BASE_URL", chat)
    env.setdefault("AGENTIX_GATEWAY_URL", gateway)
    return env


class GrokAdapter:
    name = "grok"

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}
        self.command = self.cfg.get("command") or "grok"

    def run_role_turn(
        self,
        role: str,
        prompt: str,
        handoff_in_path: Optional[Path],
        workdir: Path,
        timeout_s: int,
    ) -> Path:
        if not self.command:
            raise RuntimeError(
                "grok adapter not configured in project_config.supervisor.adapters.grok"
            )
        if not shutil.which(self.command):
            raise RuntimeError(f"{self.command} not on PATH")
        assert_ready(workdir, adapter_name="grok")
        env = os.environ.copy()
        env["AGENTIX_PROJECT_ROOT"] = str(Path(workdir).resolve())
        try:
            apply_proxy_env(env, workdir)
        except ProxyNotReady:
            raise
        except Exception as exc:
            raise ProxyNotReady(f"proxy env: {exc}") from exc
        env = apply_stream_env(env)
        # grok --help: -p/--single PROMPT for single-turn stdout; cwd via subprocess
        cmd = [self.command, "-p", prompt]
        r = subprocess.run(
            cmd,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
        )
        combined = (r.stdout or "") + "\n" + (r.stderr or "")
        if r.returncode != 0 and not combined.strip():
            raise RuntimeError(
                f"grok failed rc={r.returncode}: {(r.stderr or '')[:500]}"
            )
        data = extract_handoff(combined)
        return persist_role_handoff(workdir, data)
