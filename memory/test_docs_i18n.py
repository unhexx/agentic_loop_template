# -*- coding: utf-8 -*-
"""Существование русских sibling Path 1 и взаимные Language-ссылки."""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_russian_siblings_exist():
    assert (REPO / "README.ru.md").is_file()
    assert (REPO / "docs" / "ru" / "getting-started.md").is_file()
    assert (REPO / "docs" / "ru" / "README.md").is_file()


def test_english_headers_point_ru():
    assert "README.ru.md" in (REPO / "README.md").read_text(encoding="utf-8")
    assert "ru/getting-started.md" in (REPO / "docs" / "getting-started.md").read_text(
        encoding="utf-8"
    )
    assert "ru/README.md" in (REPO / "docs" / "README.md").read_text(encoding="utf-8")


def test_version_anchors_and_cli_names():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    toc = readme.split("## pxpipe", 1)[0]
    assert "Gemini 3.8" in toc
    assert "Gemini 3.7" not in toc
    assert "agentix-doctor" in readme
    assert "agentix-local-mcp" in readme
    assert "paste-prompt" in readme
    memory_readme = (REPO / "memory" / "README.md").read_text(encoding="utf-8")
    assert "version-3.13.0" in memory_readme
    assert "agentic_loop_template.memory" not in memory_readme
    proxy = (REPO / "docs" / "proxy.md").read_text(encoding="utf-8")
    assert "version-3.13.0" in proxy
    historical = (REPO / "AGENTIC_LOOP_README.md").read_text(encoding="utf-8")
    assert "Исторический снимок" in historical
    assert "version-3.4.0" in historical


def test_russian_headers_point_en():
    assert "](README.md)" in (REPO / "README.ru.md").read_text(encoding="utf-8")
    assert "../getting-started.md" in (
        REPO / "docs" / "ru" / "getting-started.md"
    ).read_text(encoding="utf-8")
    assert "../README.md" in (REPO / "docs" / "ru" / "README.md").read_text(
        encoding="utf-8"
    )
