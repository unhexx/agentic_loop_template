# -*- coding: utf-8 -*-
"""
Тесты политики прокси и health. Живой pxpipe/сеть не нужны.

Запуск:
  python -m memory.test_proxy
  pytest memory/test_proxy.py -q
"""

from __future__ import annotations

import io
import json
import os
import socket
import tempfile
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import Dict, Iterator, Optional

from memory.proxy.config import (
    DEFAULT_INSTALL_CHAT_PROXY,
    DEFAULT_MODE,
    effective_mode,
    load_proxy_config,
)
from memory.proxy.health import health_report, tcp_ok
from memory.proxy.install import MARKER, MARKER_END, install_venv
from memory.proxy.policy import (
    ProxyNotReady,
    adapter_requires_proxy,
    assert_ready,
    init_should_fail,
)
from memory.proxy.__main__ import cli


@contextmanager
def _env(**kwargs: Optional[str]) -> Iterator[None]:
    """Временно подставляем/снимаем переменные окружения."""
    keys = list(kwargs.keys())
    saved: Dict[str, Optional[str]] = {k: os.environ.get(k) for k in keys}
    try:
        for k, v in kwargs.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


def _write_cfg(root: Path, payload: dict) -> Path:
    agent = root / ".agent"
    agent.mkdir(parents=True)
    path = agent / "project_config.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _listen() -> socket.socket:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(64)
    return srv


def test_tcp_ok_open_and_closed_port() -> None:
    srv = _listen()
    host, port = srv.getsockname()[:2]
    try:
        assert tcp_ok(host, port) is True
    finally:
        srv.close()
    assert tcp_ok(host, port) is False


def test_mode_matrix_env_beats_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_cfg(root, {"proxy": {"mode": "required"}, "supervisor": {"adapter": "grok"}})
        with _env(
            AGENTIX_PROXY=None,
            AGENTIX_PROXY_MODE=None,
            AGENTIX_PXPIPE_URL=None,
            GROK_CLI_CHAT_PROXY_BASE_URL=None,
            AGENTIX_PROJECT_ROOT=str(root),
        ):
            cfg = load_proxy_config(root)
            assert cfg["mode"] == "required"
            assert effective_mode(cfg) == "required"
        with _env(AGENTIX_PROXY="0", AGENTIX_PROJECT_ROOT=str(root)):
            cfg = load_proxy_config(root)
            assert effective_mode(cfg) == "off"
            assert_ready(root, adapter_name="grok")  # off — не бросаем
        with _env(
            AGENTIX_PROXY="1",
            AGENTIX_PROXY_MODE="preferred",
            AGENTIX_PROJECT_ROOT=str(root),
        ):
            cfg = load_proxy_config(root)
            assert effective_mode(cfg) == "preferred"
        with _env(AGENTIX_PROXY="off", AGENTIX_PROJECT_ROOT=str(root)):
            assert load_proxy_config(root)["mode"] == "off"


def test_missing_proxy_section_defaults_to_required() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_cfg(root, {"supervisor": {"adapter": "mock"}})
        with _env(
            AGENTIX_PROXY=None,
            AGENTIX_PROXY_MODE=None,
            AGENTIX_PROJECT_ROOT=str(root),
        ):
            cfg = load_proxy_config(root)
            assert cfg["mode"] == DEFAULT_MODE == "required"


def test_mock_skips_assert_ready_even_if_port_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_cfg(
            root,
            {
                "proxy": {
                    "mode": "required",
                    "pxpipe_base": "http://127.0.0.1:1",
                },
                "supervisor": {"adapter": "mock"},
            },
        )
        with _env(
            AGENTIX_PROXY=None,
            AGENTIX_PROXY_MODE=None,
            AGENTIX_PXPIPE_URL="http://127.0.0.1:1",
            AGENTIX_PROJECT_ROOT=str(root),
        ):
            assert adapter_requires_proxy("mock") is False
            assert_ready(root, adapter_name="mock")
            assert init_should_fail(root, frontend="mock") is False


def test_required_grok_raises_when_pxpipe_down() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_cfg(
            root,
            {
                "proxy": {"mode": "required", "pxpipe_base": "http://127.0.0.1:1"},
                "supervisor": {"adapter": "grok"},
            },
        )
        with _env(
            AGENTIX_PROXY=None,
            AGENTIX_PROXY_MODE=None,
            AGENTIX_PXPIPE_URL="http://127.0.0.1:1",
            AGENTIX_PROJECT_ROOT=str(root),
        ):
            raised = False
            try:
                assert_ready(root, adapter_name="grok")
            except ProxyNotReady as exc:
                raised = True
                assert "pxpipe" in str(exc).lower() or "required" in str(exc).lower()
            assert raised
            assert init_should_fail(root, frontend="grok") is True


def test_required_grok_ok_with_fake_listener() -> None:
    srv = _listen()
    host, port = srv.getsockname()[:2]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = f"http://{host}:{port}"
            _write_cfg(
                root,
                {
                    "proxy": {"mode": "required", "pxpipe_base": base},
                    "supervisor": {"adapter": "grok"},
                },
            )
            with _env(
                AGENTIX_PROXY=None,
                AGENTIX_PROXY_MODE=None,
                AGENTIX_PXPIPE_URL=base,
                AGENTIX_PROJECT_ROOT=str(root),
            ):
                assert_ready(root, adapter_name="grok")
                report = health_report(root, frontend="grok")
                assert report["pxpipe_ok"] is True
                assert report["ok"] is True
                assert init_should_fail(root, frontend="grok") is False
    finally:
        srv.close()


def test_preferred_does_not_raise_when_down() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_cfg(
            root,
            {
                "proxy": {"mode": "preferred", "pxpipe_base": "http://127.0.0.1:1"},
                "supervisor": {"adapter": "grok"},
            },
        )
        with _env(
            AGENTIX_PROXY=None,
            AGENTIX_PROXY_MODE="preferred",
            AGENTIX_PXPIPE_URL="http://127.0.0.1:1",
            AGENTIX_PROJECT_ROOT=str(root),
        ):
            assert_ready(root, adapter_name="grok")
            assert init_should_fail(root, frontend="grok") is False


def test_install_venv_writes_marker_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        act = root / ".venv" / "bin" / "activate"
        act.parent.mkdir(parents=True)
        act.write_text("# fake activate\n", encoding="utf-8")
        first = install_venv(root=root)
        assert first["ok"]
        text = act.read_text(encoding="utf-8")
        assert MARKER in text
        assert MARKER_END in text
        assert DEFAULT_INSTALL_CHAT_PROXY in text
        assert "GROK_CLI_CHAT_PROXY_BASE_URL" in text
        second = install_venv(root=root)
        text2 = act.read_text(encoding="utf-8")
        assert text.count(MARKER_END) == 1
        assert text2.count(MARKER_END) == 1
        assert second["written"] == [] or MARKER in text2


def test_cli_health_json_and_init_mock() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_cfg(
            root,
            {
                "proxy": {"mode": "required", "pxpipe_base": "http://127.0.0.1:1"},
                "supervisor": {"adapter": "mock"},
            },
        )
        with _env(
            AGENTIX_PROXY=None,
            AGENTIX_PROXY_MODE=None,
            AGENTIX_PXPIPE_URL="http://127.0.0.1:1",
            AGENTIX_PROJECT_ROOT=str(root),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli(
                    [
                        "health",
                        "--json",
                        "--init",
                        "--workdir",
                        str(root),
                        "--frontend",
                        "mock",
                    ]
                )
            assert rc == 0
            payload = json.loads(buf.getvalue())
            assert payload["adapter_exempt"] is True
            assert payload["pxpipe_ok"] is False


def test_grok_adapter_calls_assert_ready(monkeypatch=None) -> None:
    """GrokAdapter.run_role_turn должен упасть на политике до subprocess."""
    import memory.adapters.grok as grok_mod
    from memory.adapters.grok import GrokAdapter

    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise ProxyNotReady("blocked in test")

    orig = grok_mod.assert_ready
    grok_mod.assert_ready = _boom  # type: ignore[assignment]
    try:
        ad = GrokAdapter({"command": "grok"})
        with tempfile.TemporaryDirectory() as tmp:
            raised = False
            try:
                ad.run_role_turn(
                    role="Coder",
                    prompt="x",
                    handoff_in_path=None,
                    workdir=Path(tmp),
                    timeout_s=5,
                )
            except ProxyNotReady:
                raised = True
            assert raised
            assert called["n"] == 1
    finally:
        grok_mod.assert_ready = orig


def _run_all() -> None:
    tests = [
        test_tcp_ok_open_and_closed_port,
        test_mode_matrix_env_beats_file,
        test_missing_proxy_section_defaults_to_required,
        test_mock_skips_assert_ready_even_if_port_closed,
        test_required_grok_raises_when_pxpipe_down,
        test_required_grok_ok_with_fake_listener,
        test_preferred_does_not_raise_when_down,
        test_install_venv_writes_marker_idempotent,
        test_cli_health_json_and_init_mock,
        test_grok_adapter_calls_assert_ready,
    ]
    for fn in tests:
        fn()
        print(f"OK {fn.__name__}")
    print(f"all {len(tests)} proxy tests passed")


if __name__ == "__main__":
    _run_all()
