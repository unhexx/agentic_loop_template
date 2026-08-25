# -*- coding: utf-8 -*-
"""Настройка логов memory.*: один StreamHandler, редактура на логгере."""

from __future__ import annotations

import logging
import os

_CHILD_LOGGERS = (
    "memory.supervisor",
    "memory.proxy.config",
    "memory.proxy.gateway",
    "memory.playbooks",
    "memory.adapters",  # фильтры родителя memory на child не действуют
)


def _attach_redact(logger: logging.Logger) -> None:
    from memory.dashboard.redact import RedactFilter  # stdlib; без FastAPI

    if not any(isinstance(f, RedactFilter) for f in logger.filters):
        logger.addFilter(RedactFilter())


def get_logger(name: str) -> logging.Logger:
    """Как getLogger, но с RedactFilter на самом эмиттере.

    Фильтры родителя memory не применяются к записям child-логгеров.
    """
    lg = logging.getLogger(name)
    _attach_redact(lg)
    return lg


def configure_logging() -> None:
    """Вешает stderr и RedactFilter на логгер memory, не на хендлер.

    Фильтр на логгере, чтобы caplog и любой поздний хендлер не обошли маскировку.
    """
    level = os.environ.get("AGENTIX_LOG_LEVEL", "INFO").upper()
    root = logging.getLogger("memory")
    _attach_redact(root)
    for name in _CHILD_LOGGERS:
        _attach_redact(logging.getLogger(name))
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))
    root.propagate = False
