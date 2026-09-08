# -*- coding: utf-8 -*-
"""Контракт операторского Compose-стека: loopback, профили, pxpipe, JSON SearXNG."""

from __future__ import annotations

from pathlib import Path

from memory.dashboard.config import DEFAULT_PORT
from memory.proxy.config import GATEWAY_PORT, PXPIPE_PORT
from memory.stack import STACK, cli, compose_path, validate_stack_files

REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "deploy" / "compose.yaml"
ENV_EX = REPO / "deploy" / "compose.env.example"
SETTINGS = REPO / "deploy" / "searxng" / "settings.yml"
LIMITER = REPO / "deploy" / "searxng" / "limiter.toml"
SCRIPT = REPO / "scripts" / "agentix-stack.sh"


def test_deploy_files_exist():
    assert COMPOSE.is_file()
    assert ENV_EX.is_file()
    assert SETTINGS.is_file()
    assert LIMITER.is_file()
    assert SCRIPT.is_file()
    limiter = LIMITER.read_text(encoding="utf-8")
    assert "127.0.0.0/8" in limiter
    assert SCRIPT.stat().st_mode & 0o111, "agentix-stack.sh должен быть исполняемым"


def test_validate_stack_files_ok():
    report = validate_stack_files(REPO)
    assert report["ok"] is True
    assert report["errors"] == []


def test_compose_follows_stack_contract():
    """Дрейф YAML относительно STACK должен валить тест, даже если валидатор ослабят."""
    text = COMPOSE.read_text(encoding="utf-8")
    for name, svc in STACK.items():
        mapping = svc.publish_mapping()
        if mapping:
            assert mapping in text, name
        needles = svc.compose_profile_needles()
        if needles:
            assert any(n in text for n in needles), name
        if svc.image:
            assert svc.image in text, name
        if not svc.publish and svc.runtime == "compose" and svc.host_port is not None:
            assert f"{svc.host_port}:{svc.host_port}" not in text, name
    # LDR_WEB_HOST слушает внутри сети; на хост 0.0.0.0 публиковать нельзя (SR-04).
    assert "0.0.0.0:" not in text.replace("LDR_WEB_HOST=0.0.0.0", "")


def test_host_ports_come_from_config_modules():
    assert STACK["searxng"].host_port == 8080
    assert STACK["ldr"].host_port == 5000
    assert STACK["gateway"].host_port == GATEWAY_PORT
    assert STACK["pxpipe"].host_port == PXPIPE_PORT
    assert STACK["dashboard"].host_port == DEFAULT_PORT
    assert compose_path(REPO) == COMPOSE


def test_compose_research_uses_gateway_pxpipe_path():
    text = COMPOSE.read_text(encoding="utf-8")
    env = ENV_EX.read_text(encoding="utf-8")
    blob = text + "\n" + env
    gw = STACK["gateway"].host_port
    sx = STACK["searxng"].host_port
    assert f"host.docker.internal:{gw}" in blob
    assert "openai_endpoint" in blob
    assert f"searxng:{sx}" in blob


def test_searxng_settings_beyond_validator():
    """json/public_instance уже в validate_stack_files; здесь — секрет и html."""
    text = SETTINGS.read_text(encoding="utf-8")
    assert "use_default_settings: true" in text
    assert "- html" in text
    assert "- json" in text
    assert 'secret_key: "ultrasecretkey"' not in text


def test_stack_cli_json(capsys):
    rc = cli(["contract", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"searxng"' in out
    assert str(STACK["searxng"].host_port) in out


def test_stack_cli_check():
    assert cli(["check"]) == 0


def test_env_example_has_no_real_secret_placeholder_only():
    text = ENV_EX.read_text(encoding="utf-8")
    assert "SEARXNG_SECRET=" in text
    assert "change-me" in text.lower() or "replace" in text.lower()
    assert "LDR_LLM_OPENAI_ENDPOINT_URL=" in text


def test_gitignore_compose_env():
    gi = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "deploy/compose.env" in gi
