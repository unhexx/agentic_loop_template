# -*- coding: utf-8 -*-
"""Denylist for environment_variables and log redaction for memory.local_mcp."""

from __future__ import annotations

import logging
import re
from typing import Any, Mapping, NamedTuple, Optional

from memory.dashboard.redact import redact_tokens

# Prefix match is only at the start. DASHBOARD_TOKEN is the one exact name.
_KEY_PREFIXES = (
    "GITHUB_",
    "GH_",
    "AWS_",
    "ACTIONS_",
    "JENKINS_",
    "BITBUCKET_",
    "SLACK_",
    "JIRA_",
    "LINEAR_",
    "NPM_",
    "PYPI_",
    "BLACKBOX_",
)
_EXACT_KEYS = frozenset({"DASHBOARD_TOKEN"})

# Substring scan, not a full-value regex. The trailing space on BEGIN is required.
# gho_, ghu_, ghs_, and ghr_ are the other live GitHub token prefixes, same as ghp_.
_VALUE_MARKERS = (
    "-----BEGIN ",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
)

_KEY_CODE = "secret_key_rejected"
_VALUE_CODE = "secret_value_rejected"
_KEY_MESSAGE = "environment variable key is not allowed"
_VALUE_MESSAGE = "environment variable value is not allowed"
_MASK = "****"

# A later same-line KEY=value ends an unquoted secret. Do not cross newlines.
_NEXT_ASSIGN = r"[A-Z_][A-Z0-9_]{0,63}="
_ASSIGN_RE = re.compile(
    r"\b((?:DASHBOARD_TOKEN|(?:"
    + "|".join(re.escape(prefix) for prefix in sorted(_KEY_PREFIXES, key=len, reverse=True))
    + r")[A-Z0-9_]*)=)"
    + r'(?:"[^"\n]*"'
    + r"|'[^'\n]*'"
    + r"|\S+(?:[ \t]+(?!"
    + _NEXT_ASSIGN
    + r")\S+)*)"
)
# ghp_, gho_, ghu_, ghs_, ghr_. github_pat_ does not match (third character is i).
_GHP_RE = re.compile(r"gh[oprsu]_\S*")
_PAT_RE = re.compile(r"github_pat_\S*")
# Full block when END is in the same string. With no END, the header and every
# following line are masked, the same span stream._mask_pem keeps until END.
_PEM_RE = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+-----.*?-----END [A-Z0-9 ]+-----|.*)",
    re.DOTALL,
)


class EnvRejection(NamedTuple):
    code: str
    message: str


def contains_secret_marker(text: str) -> bool:
    return any(marker in text for marker in _VALUE_MARKERS)


def clip_message(text: str, limit: int = 500) -> str:
    """One line for a protocol or error field. Newlines become spaces."""
    flat = text.replace("\r", " ").replace("\n", " ")
    if len(flat) > limit:
        return flat[:limit]
    return flat


def _key_denied(key: object) -> bool:
    if not isinstance(key, str):
        return False
    if key in _EXACT_KEYS:
        return True
    return any(key.startswith(prefix) for prefix in _KEY_PREFIXES)


def _value_denied(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return contains_secret_marker(value)


def check_environment(env: Optional[Mapping[str, Any]]) -> Optional[EnvRejection]:
    """None when clean. Key rejection wins. The message does not echo values."""
    if not isinstance(env, Mapping):
        return None
    for key in env:
        if _key_denied(key):
            return EnvRejection(_KEY_CODE, _KEY_MESSAGE)
    for value in env.values():
        if _value_denied(value):
            return EnvRejection(_VALUE_CODE, _VALUE_MESSAGE)
    return None


def _mask_pipeline_secrets(text: str) -> str:
    """GitHub/PEM markers and denylist assignments. Does not touch other tokens."""
    red = _PAT_RE.sub(_MASK, text)
    red = _GHP_RE.sub(_MASK, red)
    red = _PEM_RE.sub(_MASK, red)
    return _ASSIGN_RE.sub(lambda match: match.group(1) + _MASK, red)


def redact_text(text: str) -> str:
    """Dashboard patterns, then GitHub/PEM markers and denylist assignments."""
    red = redact_tokens(text)
    if not red:
        return red
    return _mask_pipeline_secrets(red)


class LocalRedactFilter(logging.Filter):
    """GitHub/PEM on the raw message. The dashboard filter runs next."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            rendered = str(record.msg)
        # redact_text full-masks DASHBOARD_TOKEN and would defeat keep_edges.
        record.msg = _mask_pipeline_secrets(rendered)
        record.args = ()
        if isinstance(record.exc_text, str) and record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        elif record.exc_info:
            record.exc_text = redact_text(
                logging.Formatter().formatException(record.exc_info)
            )
        if isinstance(record.stack_info, str):
            record.stack_info = redact_text(record.stack_info)
        return True


def install(logger: logging.Logger) -> None:
    if not any(isinstance(filt, LocalRedactFilter) for filt in logger.filters):
        logger.addFilter(LocalRedactFilter())
