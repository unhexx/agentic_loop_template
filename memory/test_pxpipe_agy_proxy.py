# -*- coding: utf-8 -*-
"""Smoke для scripts/pxpipe-agy/proxy.mjs: Host не выбирает upstream."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEST = ROOT / "scripts" / "pxpipe-agy" / "proxy.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_pxpipe_agy_proxy_pins_upstream() -> None:
    proc = subprocess.run(
        ["node", "--test", str(TEST)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
