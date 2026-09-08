# -*- coding: utf-8 -*-
"""Контракт операторского Compose-стека: loopback, профили, pxpipe, JSON SearXNG."""

from __future__ import annotations

from pathlib import Path
from shutil import copy2, copytree

from memory.dashboard.config import DEFAULT_PORT
from memory.proxy.config import GATEWAY_PORT, PXPIPE_PORT
from memory.stack import (
    STACK,
    cli,
    compose_path,
    compose_service_block,
    validate_stack_files,
)

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


def test_searxng_has_no_compose_profile():
    """SearXNG всегда с проектом; профили остаются только у research/ollama."""
    assert STACK["searxng"].profile is None
    assert STACK["searxng"].compose_profile_needles() == ()
    assert STACK["ldr"].profile == "research"
    assert STACK["ollama"].profile == "ollama"
    text = COMPOSE.read_text(encoding="utf-8")
    block = compose_service_block(text, "searxng")
    assert block
    for line in block.splitlines():
        if line.lstrip().startswith("#"):
            continue
        assert not line.lstrip().startswith("profiles:"), line
    assert "search" not in {STACK[k].profile for k in STACK}


def test_validate_rejects_leftover_searxng_profile(tmp_path: Path):
    """Ключ profiles на always-on службе прячет её от compose up без --profile."""
    (tmp_path / "VERSION").write_text((REPO / "VERSION").read_text(encoding="utf-8"))
    copytree(REPO / "deploy", tmp_path / "deploy")
    (tmp_path / "scripts").mkdir()
    copy2(SCRIPT, tmp_path / "scripts" / "agentix-stack.sh")
    compose = tmp_path / "deploy" / "compose.yaml"
    compose.write_text(
        COMPOSE.read_text(encoding="utf-8").replace(
            "  searxng:\n    image:",
            '  searxng:\n    profiles: ["search"]\n    image:',
        ),
        encoding="utf-8",
    )
    report = validate_stack_files(tmp_path)
    assert report["ok"] is False
    assert any("must not declare a compose profile" in e for e in report["errors"])


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
    assert "agentix-local-dev-not-ultrasecretkey" in text


def test_searxng_secret_is_settings_not_env():
    """Один источник секрета: закоммиченный ключ. Env SEARXNG_SECRET не держим."""
    compose = COMPOSE.read_text(encoding="utf-8")
    env = ENV_EX.read_text(encoding="utf-8")
    assert "SEARXNG_SECRET" not in compose
    assert "SEARXNG_SECRET" not in env


def test_searxng_volumes_named_and_readonly_config():
    text = COMPOSE.read_text(encoding="utf-8")
    assert "./deploy/searxng:/etc/searxng" not in text
    assert "searxng_data:/etc/searxng" in text
    assert "settings.yml:/etc/searxng/settings.yml:ro" in text
    assert "limiter.toml:/etc/searxng/limiter.toml:ro" in text


def test_stack_cli_json(capsys):
    rc = cli(["contract", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"searxng"' in out
    assert str(STACK["searxng"].host_port) in out


def test_stack_cli_check():
    assert cli(["check"]) == 0


def test_env_example_has_ldr_endpoint_no_searx_secret():
    text = ENV_EX.read_text(encoding="utf-8")
    assert "LDR_LLM_OPENAI_ENDPOINT_URL=" in text
    assert "SEARXNG_SECRET" not in text


def test_agentix_stack_script_portable_and_fail_closed():
    text = SCRIPT.read_text(encoding="utf-8")
    # Команда in-place sed ломает BSD sed на macOS — в скрипте её быть не должно.
    assert not any(ln.lstrip().startswith("sed ") for ln in text.splitlines())
    assert "PROFILES=(search)" not in text
    assert "memory.stack check" in text
    assert "/healthz" in text
    health = text.split("health)", 1)[1]
    assert "memory.stack check" in health
    assert "exit 1" in health


def test_gitignore_compose_env():
    gi = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "deploy/compose.env" in gi
