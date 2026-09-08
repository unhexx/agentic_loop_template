# -*- coding: utf-8 -*-
"""Контракт операторского Compose-стека: loopback, профили, pxpipe, JSON SearXNG."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "deploy" / "compose.yaml"
ENV_EX = REPO / "deploy" / "compose.env.example"
SETTINGS = REPO / "deploy" / "searxng" / "settings.yml"
SCRIPT = REPO / "scripts" / "agentix-stack.sh"


def test_deploy_files_exist():
    assert COMPOSE.is_file()
    assert ENV_EX.is_file()
    assert SETTINGS.is_file()
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111, "agentix-stack.sh должен быть исполняемым"


def test_compose_profiles_and_loopback():
    text = COMPOSE.read_text(encoding="utf-8")
    for name in ("searxng", "local-deep-research", "ollama"):
        assert name in text
    for profile in ("search", "research", "ollama"):
        assert profile in text
    assert "127.0.0.1:8080:8080" in text
    assert "127.0.0.1:5000:5000" in text
    assert "0.0.0.0:" not in text.replace("LDR_WEB_HOST=0.0.0.0", "")
    assert "host.docker.internal:host-gateway" in text
    assert "no-new-privileges:true" in text


def test_compose_does_not_publish_ollama():
    text = COMPOSE.read_text(encoding="utf-8")
    assert "11434:11434" not in text


def test_compose_research_uses_gateway_pxpipe_path():
    text = COMPOSE.read_text(encoding="utf-8")
    env = ENV_EX.read_text(encoding="utf-8")
    blob = text + "\n" + env
    assert "host.docker.internal:8110" in blob
    assert "openai_endpoint" in blob
    assert "searxng:8080" in blob


def test_searxng_json_format_enabled():
    text = SETTINGS.read_text(encoding="utf-8")
    assert "use_default_settings: true" in text
    assert "json" in text
    assert "html" in text
    assert "public_instance: false" in text
    assert 'secret_key: "ultrasecretkey"' not in text


def test_stack_contract_matches_files():
    from memory.stack import STACK, compose_path, validate_stack_files

    assert STACK["searxng"]["host_port"] == 8080
    assert STACK["ldr"]["host_port"] == 5000
    assert STACK["pxpipe"]["host_port"] == 8100
    assert STACK["gateway"]["host_port"] == 8110
    assert compose_path(REPO) == COMPOSE
    report = validate_stack_files(REPO)
    assert report["ok"] is True
    assert report["errors"] == []


def test_stack_cli_json(capsys):
    from memory.stack import cli

    rc = cli(["contract", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"searxng"' in out
    assert "8080" in out


def test_stack_cli_check():
    from memory.stack import cli

    assert cli(["check"]) == 0


def test_env_example_has_no_real_secret_placeholder_only():
    text = ENV_EX.read_text(encoding="utf-8")
    assert "SEARXNG_SECRET=" in text
    assert "change-me" in text.lower() or "replace" in text.lower()
    assert "LDR_LLM_OPENAI_ENDPOINT_URL=" in text


def test_gitignore_compose_env():
    gi = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "deploy/compose.env" in gi
