# -*- coding: utf-8 -*-
"""Одна команда: поставить Agentix в новый проект и заполнить стартовые файлы.

Не копирует дерево шаблона. Кладёт симлинк на SSOT, editable-установку в
``.venv`` и файлы из ``examples/consumer-starter`` с уже подставленными полями.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

FRONTENDS = ("grok", "cursor", "claude", "blackbox")
SUBPROCESS_ADAPTERS = ("grok", "cursor", "blackbox")
TIERS = ("lite", "full")
GITIGNORE_MARKER = "# Agentix template and cycle artifacts"


class InitError(Exception):
    """Установка в проект невозможна без догадок со стороны вызывающего."""


def resolve_template(explicit: Path | str | None = None) -> Path:
    """Каталог SSOT: явный путь, ``AGENTIX_TEMPLATE`` или репозиторий пакета."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("AGENTIX_TEMPLATE")
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve().parents[1]
    candidates.append(here)
    candidates.append(Path.cwd())
    seen: set[Path] = set()
    for raw in candidates:
        path = raw.expanduser().resolve()
        if path in seen:
            continue
        seen.add(path)
        if (path / "VERSION").is_file() and (path / "examples" / "consumer-starter").is_dir():
            return path
    raise InitError(
        "agentic_loop_template не найден. Передайте --template /path/to/agentic_loop_template"
    )


def venv_python(target: Path) -> Path:
    if os.name == "nt":
        return target / ".venv" / "Scripts" / "python.exe"
    return target / ".venv" / "bin" / "python"


def ritual_argv(python: Path, frontend: str) -> list[list[str]]:
    """Холодный старт, тот же набор, что у Agent-Init (ошибки отдельных шагов не фатальны)."""
    py = str(python)
    return [
        [py, "-m", "memory", "state", "init"],
        [py, "-m", "memory.experience_harvester", "seed-defaults", "--apply"],
        [py, "-m", "memory.knowledge", "ingest-if-empty", "--root", "docs", "--budget", "800"],
        [py, "-m", "memory.playbooks", "seed", "--from-standards"],
        [py, "-m", "memory.proxy", "install-venv"],
        [py, "-m", "memory.context_budget", "cold-start", "--budget", "16000", "--compress"],
        [py, "-m", "memory.proxy", "health", "--init", "--frontend", frontend],
    ]


def _fill(text: str, mapping: dict[str, str]) -> str:
    text = text.replace(
        "Copy to repo root as `AGENTS.md` and fill the `{{...}}` placeholders.\n",
        "",
    )
    for key, value in mapping.items():
        text = text.replace("{{" + key + "}}", value)
    leftover = sorted(set(re.findall(r"\{\{[A-Z0-9_]+\}\}", text)))
    if leftover:
        raise InitError("в шаблоне остались placeholder: " + ", ".join(leftover))
    return text


def _mapping(name: str, goal: str, stack: str) -> dict[str, str]:
    return {
        "PROJECT_NAME": name,
        "ONE_SENTENCE_GOAL": goal,
        "PRIMARY_GOAL": goal,
        "STATUS_ONE_LINER": "Bootstrap complete. First cycle has not started.",
        "STACK": stack,
        "TECH_STACK": stack,
        "PKG": "pip",
        "TEST_CMD": "pytest -q",
        "LINT_CMD": "python -m compileall -q .",
        "INSTALL_CMD": "python -m pip install -e .",
        "RUN_CMD": "python -m memory info",
        "SPEC_FILE": "TASK_SPECIFICATION.md",
        "OBJECTIVE_1": "Bootstrap the Agentix loop in this repository",
        "OBJECTIVE_2": "Ship the first INVEST task with tests",
        "IN_SCOPE_LIST": "application code, tests, and docs for this repository",
        "OUT_OF_SCOPE_LIST": "vendoring a copy of agentic_loop_template",
    }


def _log(quiet: bool, message: str) -> None:
    if not quiet:
        print(f"[agentix-init] {message}")


def _refuse_symlink(path: Path) -> None:
    if path.is_symlink():
        raise InitError(f"отказ писать по симлинку {path}")


def _write(path: Path, content: str, *, force: bool, created: list[str], skipped: list[str]) -> None:
    _refuse_symlink(path)
    rel = path.name if path.parent.name != ".agent" else f".agent/{path.name}"
    if path.is_file() and not force:
        skipped.append(rel)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    created.append(rel)


def _link_template(target: Path, template: Path, *, created: list[str], skipped: list[str]) -> None:
    link = target / "agentic_loop_template"
    if link.exists() or link.is_symlink():
        try:
            same = link.resolve() == template.resolve()
        except OSError:
            same = False
        if same:
            skipped.append("agentic_loop_template")
            return
        raise InitError(
            f"{link} уже существует и не указывает на {template}. "
            "Не заменяю чужое дерево шаблона."
        )
    try:
        dest: Path | str = Path(os.path.relpath(template, target))
    except ValueError:
        dest = template
    link.symlink_to(dest)
    created.append("agentic_loop_template")


def _merge_gitignore(target: Path, block: str, *, force: bool, created: list[str], skipped: list[str]) -> None:
    path = target / ".gitignore"
    _refuse_symlink(path)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if GITIGNORE_MARKER in existing and not force:
        skipped.append(".gitignore")
        return
    if GITIGNORE_MARKER in existing and force:
        skipped.append(".gitignore")
        return
    piece = block.strip() + "\n"
    merged = existing
    if merged and not merged.endswith("\n"):
        merged += "\n"
    if merged:
        merged += "\n"
    merged += piece
    path.write_text(merged, encoding="utf-8")
    created.append(".gitignore")


def _load_config(template: Path) -> dict:
    example = template / ".agent" / "project_config.example.json"
    if not example.is_file():
        raise InitError(f"нет {example}")
    data = json.loads(example.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise InitError("project_config.example.json должен быть объектом")
    return data


def _apply_project(cfg: dict, *, name: str, goal: str, frontend: str, tier: str) -> None:
    cfg["project"] = {
        "name": name,
        "goal": goal,
        "tier": tier,
        "frontend": frontend,
        "spec_file": "TASK_SPECIFICATION.md",
    }
    supervisor = cfg.get("supervisor")
    if isinstance(supervisor, dict):
        supervisor["adapter"] = frontend if frontend in SUBPROCESS_ADAPTERS else "mock"


def _ensure_venv(target: Path, template: Path, python: str) -> Path:
    py = venv_python(target)
    if not py.is_file():
        subprocess.run([python, "-m", "venv", str(target / ".venv")], check=True)
        subprocess.run([str(py), "-m", "pip", "install", "-U", "pip", "-q"], check=False)
    install = subprocess.run(
        [str(py), "-m", "pip", "install", "-e", f"{template}[dev]"],
        check=False,
    )
    if install.returncode != 0:
        fallback = subprocess.run(
            [str(py), "-m", "pip", "install", "jsonschema>=4.18,<5", "pytest>=8.0,<9"],
            check=False,
        )
        if fallback.returncode != 0:
            raise InitError(f"pip install -e {template}[dev] не удался")
    return py


def _run_ritual(target: Path, python: Path, template: Path, frontend: str) -> list[str]:
    env = os.environ.copy()
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(template) if not prev else str(template) + os.pathsep + prev
    warnings: list[str] = []
    for cmd in ritual_argv(python, frontend):
        result = subprocess.run(
            cmd,
            cwd=target,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            warnings.append(" ".join(cmd[1:]) + f" -> exit {result.returncode}")
    return warnings


def init_project(
    target: Path | str,
    *,
    template: Path | str | None = None,
    name: str | None = None,
    goal: str | None = None,
    stack: str | None = None,
    frontend: str = "grok",
    tier: str = "full",
    venv: bool = True,
    ritual: bool = True,
    force: bool = False,
    quiet: bool = False,
    python: str | None = None,
) -> dict:
    """Создать или донастроить проект. Повторный запуск не затирает существующие файлы."""
    frontend = (frontend or "grok").strip().lower()
    tier = (tier or "full").strip().lower()
    if frontend not in FRONTENDS:
        raise InitError(f"frontend должен быть одним из {', '.join(FRONTENDS)}")
    if tier not in TIERS:
        raise InitError(f"tier должен быть одним из {', '.join(TIERS)}")

    template_path = resolve_template(template)
    target_path = Path(target).expanduser().resolve()
    if target_path == template_path:
        raise InitError("цель совпадает с каталогом шаблона; укажите каталог нового проекта")
    if target_path.is_file():
        raise InitError(f"{target_path} — файл, нужен каталог проекта")
    try:
        template_path.relative_to(target_path)
    except ValueError:
        inside = False
    else:
        inside = True
    if inside:
        raise InitError("каталог шаблона лежит внутри цели; укажите соседний каталог проекта")

    project_name = (name or target_path.name).strip() or target_path.name
    project_goal = (goal or "Deliver the first vertical slice in TASK_SPECIFICATION.md").strip()
    project_stack = (stack or "Python 3.10+").strip()
    mapping = _mapping(project_name, project_goal, project_stack)
    starter = template_path / "examples" / "consumer-starter"

    target_path.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    skipped: list[str] = []
    _link_template(target_path, template_path, created=created, skipped=skipped)

    agents = (starter / "AGENTS.md.example").read_text(encoding="utf-8")
    _write(target_path / "AGENTS.md", _fill(agents, mapping), force=force, created=created, skipped=skipped)

    ignore_block = (starter / ".gitignore.agentic").read_text(encoding="utf-8")
    _merge_gitignore(target_path, ignore_block, force=force, created=created, skipped=skipped)

    env_example = (starter / "agentic.env.example").read_text(encoding="utf-8")
    env_text = env_example.replace("your-org/your-project", project_name)
    _write(target_path / ".env.agentic", env_text, force=False, created=created, skipped=skipped)

    cfg_path = target_path / ".agent" / "project_config.json"
    if cfg_path.is_file() and not force:
        skipped.append(".agent/project_config.json")
    else:
        _refuse_symlink(cfg_path)
        cfg = _load_config(template_path)
        _apply_project(cfg, name=project_name, goal=project_goal, frontend=frontend, tier=tier)
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        created.append(".agent/project_config.json")

    if tier == "full":
        spec = (starter / "TASK_SPECIFICATION.example.md").read_text(encoding="utf-8")
        ctx = (starter / "PROJECT_CONTEXT.example.md").read_text(encoding="utf-8")
        _write(
            target_path / "TASK_SPECIFICATION.md",
            _fill(spec, mapping),
            force=force,
            created=created,
            skipped=skipped,
        )
        _write(
            target_path / "PROJECT_CONTEXT.md",
            _fill(ctx, mapping),
            force=force,
            created=created,
            skipped=skipped,
        )
        init_src = starter / "Agent-Init.consumer.sh"
        init_dst = target_path / "Agent-Init.sh"
        if init_dst.is_file() and not force:
            skipped.append("Agent-Init.sh")
        else:
            _refuse_symlink(init_dst)
            shutil.copyfile(init_src, init_dst)
            init_dst.chmod(0o755)
            created.append("Agent-Init.sh")
        version = (template_path / "VERSION").read_text(encoding="utf-8").strip() or "unknown"
        plan = (
            f"# .agent/PLAN.md\n\n"
            f"**Project:** {project_name}\n"
            f"**Template:** {version}\n"
            f"**Frontend:** {frontend}\n\n"
            f"## Current iteration\n\n"
            f"1. Confirm TASK_SPECIFICATION.md ({project_goal})\n"
            f"2. Orchestrator picks the first INVEST task\n"
            f"3. Coder implements; Tester runs `pytest -q`\n"
        )
        todo = (
            "# TODO\n\n"
            "- [ ] Confirm the goal in TASK_SPECIFICATION.md\n"
            "- [ ] Run the first Orchestrator cycle\n"
        )
        _write(target_path / ".agent" / "PLAN.md", plan, force=force, created=created, skipped=skipped)
        _write(target_path / ".agent" / "TODO.md", todo, force=force, created=created, skipped=skipped)
        prompt = (
            f"You are running the Agentic Development Loop (template {version}) "
            f"in {project_name}. Frontend: {frontend}.\n\n"
            "Cold-start (first, max 4 tool calls):\n"
            "1. `python -m memory.proxy health`\n"
            "2. `python -m memory state snapshot --window 3`\n"
            "3. `python -m memory query --top 5 --category \"Common Failure Patterns\"`\n"
            "4. `python -m memory.knowledge query --q \"cycle\" --top 3`\n\n"
            "Then act as **Orchestrator**:\n"
            "- prompts live in ./agentic_loop_template/prompts/short_orchestrator_prompt.md\n"
            "- Read .agent/PLAN.md and .agent/TODO.md\n"
            "- One JSON handoff; validate with python -m memory.validate_handoff\n\n"
            "Begin as Orchestrator.\n"
        )
        prompt_name = f"starter_prompt_{frontend}.txt"
        _write(
            target_path / ".agent" / prompt_name,
            prompt,
            force=force,
            created=created,
            skipped=skipped,
        )

    version = (template_path / "VERSION").read_text(encoding="utf-8").strip() or "unknown"
    py_path: Path | None = None
    warnings: list[str] = []
    if venv:
        _log(quiet, f"venv {target_path / '.venv'}")
        py_path = _ensure_venv(target_path, template_path, python or sys.executable)
    if ritual:
        runner = py_path if py_path is not None else Path(python or sys.executable)
        _log(quiet, "cold-start ritual")
        warnings = _run_ritual(target_path, runner, template_path, frontend)
        for item in warnings:
            _log(quiet, f"warning: {item}")

    _log(quiet, f"created={len(created)} skipped={len(skipped)} tier={tier} frontend={frontend}")
    if not quiet:
        activate = target_path / (".venv/Scripts/Activate.ps1" if os.name == "nt" else ".venv/bin/activate")
        print(f"Next: source {activate}")
        print(f"Prompt: {template_path / 'prompts' / 'short_orchestrator_prompt.md'}")
    return {
        "ok": True,
        "version": version,
        "target": str(target_path),
        "template": str(template_path),
        "frontend": frontend,
        "tier": tier,
        "name": project_name,
        "created": created,
        "skipped": skipped,
        "venv": str(py_path) if py_path else None,
        "warnings": warnings,
    }


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m memory init",
        description="Install Agentix into a new project and write its starter config",
    )
    parser.add_argument("target_pos", nargs="?", default=None, help="Project directory (default: .)")
    parser.add_argument("--target", default=None, help="Project directory")
    parser.add_argument("--template", default=None, help="Path to agentic_loop_template SSOT")
    parser.add_argument("--name", default=None, help="Project name (default: directory name)")
    parser.add_argument("--goal", default=None, help="One-sentence goal written into the spec")
    parser.add_argument("--stack", default=None, help="Stack line written into AGENTS.md")
    parser.add_argument("--frontend", default="grok", choices=FRONTENDS)
    parser.add_argument("--tier", default="full", choices=TIERS, help="lite = AGENTS.md; full = loop files")
    parser.add_argument("--no-venv", action="store_true", help="Skip .venv and pip install -e")
    parser.add_argument("--no-ritual", action="store_true", help="Skip state/playbooks/proxy cold start")
    parser.add_argument("--force", action="store_true", help="Overwrite generated project files")
    parser.add_argument("--quiet", "-q", action="store_true")
    parser.add_argument("--python", default=None, help="Interpreter used to create .venv")
    args = parser.parse_args(argv)
    target = args.target or args.target_pos or "."
    try:
        result = init_project(
            target,
            template=args.template,
            name=args.name,
            goal=args.goal,
            stack=args.stack,
            frontend=args.frontend,
            tier=args.tier,
            venv=not args.no_venv,
            ritual=not args.no_ritual,
            force=args.force,
            quiet=args.quiet,
            python=args.python,
        )
    except InitError as exc:
        print(f"agentix-init: {exc}", file=sys.stderr)
        return 2
    print(
        "AGENT_INIT_OK"
        f" version={result['version']}"
        f" target={result['target']}"
        f" template={result['template']}"
        f" frontend={result['frontend']}"
        f" tier={result['tier']}"
    )
    return 0
