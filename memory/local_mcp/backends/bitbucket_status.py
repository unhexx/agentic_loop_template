# -*- coding: utf-8 -*-
"""Optional Bitbucket commit status. Called only from the Jenkins backend.

HTTP 204 with an empty body is success. Cloud 201 is success too and is not
stored or JSON-decoded. Redirects are not followed.
"""

from __future__ import annotations

import base64
import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping

from memory.local_mcp.probe import _NoRedirect, _REDIRECTS, _controller_base

_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
_LOCAL_STATES = frozenset({"INPROGRESS", "SUCCESSFUL", "FAILED", "CANCELLED"})
_CLOUD_STATES = frozenset({"INPROGRESS", "SUCCESSFUL", "FAILED", "STOPPED"})
_READ_MAX = 64 * 1024
_TIMEOUT_S = 5.0

_CANCEL_WARNING = {
    "code": "legacy_cancel_mapped_failed",
    "message": "Bitbucket build-status rejected CANCELLED; posted FAILED",
}
_CANCEL_RETRY_FAILED = {
    "code": "legacy_cancel_retry_failed",
    "message": "Bitbucket build-status rejected CANCELLED; retry as FAILED was not posted",
}


@dataclass
class StatusPost:
    posted: bool
    warnings: list[dict[str, str]] = field(default_factory=list)
    # False when the call returned before a post, including a missing token.
    attempted: bool = False


def _scm(section: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = section.get("scm")
    if isinstance(raw, dict):
        return raw
    return {}


def _kind(section: Mapping[str, Any]) -> str:
    # Server/DC is the mandate. Cloud is explicit.
    if _scm(section).get("kind") == "cloud":
        return "cloud"
    return "server"


def _segment(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 200:
        return None
    if any(ch in text for ch in "/?#\\\r\n\x00 @:="):
        return None
    return text


def _commit(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if _COMMIT.fullmatch(text) is None:
        return None
    return text


def _ref(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text.startswith("refs/") or text.startswith("refs/pull/"):
        return None
    if len(text) > 256 or any(ch in text for ch in "\r\n\x00 ?#"):
        return None
    return text


def _safe_header(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if any(ch in value for ch in "\r\n\x00"):
        return None
    if len(value) > 4096:
        return None
    return value


def _auth_header(environ: Mapping[str, str]) -> str | None:
    token = _safe_header(environ.get("BITBUCKET_API_TOKEN"))
    if token is not None:
        return "Bearer " + token
    user = _safe_header(environ.get("BITBUCKET_USERNAME"))
    password = _safe_header(environ.get("BITBUCKET_APP_PASSWORD"))
    if user is None or password is None:
        return None
    raw = f"{user}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _server_base(section: Mapping[str, Any], environ: Mapping[str, str]) -> str | None:
    configured = _scm(section).get("bitbucket_url")
    # Env fills only a null JSON value. A string, even empty, is not null.
    if configured is None:
        raw = environ.get("BITBUCKET_URL")
    elif isinstance(configured, str) and configured.strip():
        raw = configured.strip()
    else:
        return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    # A NUL or a bad port must not escape as ValueError, and must not be posted.
    if any(ch in text for ch in "\r\n\x00"):
        return None
    try:
        return _controller_base(text)
    except ValueError:
        return None


def _check_url(url: str) -> bool:
    if any(ch in url for ch in "\r\n\x00"):
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    if parts.username or parts.password:
        return False
    if port is not None and (port < 1 or port > 65535):
        return False
    return True


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )


def _read_limited(response: object) -> None:
    try:
        response.read(_READ_MAX)  # type: ignore[attr-defined]
    except (OSError, http.client.HTTPException, ValueError):
        return


def _post_once(
    opener: Any,
    url: str,
    payload: dict[str, str],
    auth: str,
    timeout_s: float,
) -> int | None:
    if not _check_url(url):
        return None
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": auth,
    }
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with opener.open(request, timeout=timeout_s) as response:
            status = int(response.status)
            _read_limited(response)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        try:
            exc.read(_READ_MAX)
        except (OSError, http.client.HTTPException, ValueError):
            pass
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        ValueError,
        UnicodeError,
        http.client.HTTPException,
    ):
        return None
    if status in _REDIRECTS:
        return status
    return status


def _success(status: int | None, *, cloud: bool = False) -> bool:
    # Cloud create-build-status is 201. Server stays 200 or 204.
    if cloud:
        return status in (200, 201, 204)
    return status in (200, 204)


def post_commit_status(
    section: Mapping[str, Any],
    *,
    commit: str,
    state: str,
    key: str,
    url: str,
    ref: str | None,
    environ: Mapping[str, str],
    opener: Any | None = None,
    timeout_s: float = _TIMEOUT_S,
) -> StatusPost:
    """POST one build status. Empty 204 is success. Cloud 201 is too. No redirect follow."""
    sha = _commit(commit)
    if sha is None or state not in _LOCAL_STATES:
        return StatusPost(False)
    if not isinstance(key, str) or not key or len(key) > 64:
        return StatusPost(False)
    if any(ch in key for ch in "\r\n\x00"):
        return StatusPost(False)
    if not _check_url(url):
        return StatusPost(False)
    auth = _auth_header(environ)
    if auth is None:
        return StatusPost(False)
    kind = _kind(section)
    sent_state = state
    if kind == "cloud":
        sent_state = "STOPPED" if state == "CANCELLED" else state
        if sent_state not in _CLOUD_STATES:
            return StatusPost(False)
    body: dict[str, str] = {"key": key, "state": sent_state, "url": url}
    safe_ref = _ref(ref)
    if safe_ref is not None:
        # Cloud's field is refname. Server keeps ref. Same refs/... value.
        body["refname" if kind == "cloud" else "ref"] = safe_ref
    client = opener if opener is not None else _opener()
    if kind == "cloud":
        workspace = _segment(_scm(section).get("workspace"))
        slug = _segment(_scm(section).get("repo_slug"))
        if workspace is None or slug is None:
            return StatusPost(False, attempted=True)
        target = (
            "https://api.bitbucket.org/2.0/repositories/"
            f"{urllib.parse.quote(workspace, safe='')}/"
            f"{urllib.parse.quote(slug, safe='')}/commit/"
            f"{urllib.parse.quote(sha, safe='')}/statuses/build"
        )
        status = _post_once(client, target, body, auth, timeout_s)
        return StatusPost(_success(status, cloud=True), attempted=True)
    base = _server_base(section, environ)
    project = _segment(_scm(section).get("project_key"))
    slug = _segment(_scm(section).get("repo_slug"))
    if base is None or project is None or slug is None:
        return StatusPost(False, attempted=True)
    primary = (
        f"{base}/rest/api/latest/projects/"
        f"{urllib.parse.quote(project, safe='')}/repos/"
        f"{urllib.parse.quote(slug, safe='')}/commits/"
        f"{urllib.parse.quote(sha, safe='')}/builds"
    )
    status = _post_once(client, primary, body, auth, timeout_s)
    if _success(status):
        return StatusPost(True, attempted=True)
    # One fallback. Do not invent /rest/api/1.0/.../statuses/build.
    if status != 404:
        return StatusPost(False, attempted=True)
    legacy = f"{base}/rest/build-status/latest/commits/{urllib.parse.quote(sha, safe='')}"
    status = _post_once(client, legacy, body, auth, timeout_s)
    if _success(status):
        return StatusPost(True, attempted=True)
    if status == 400 and state == "CANCELLED":
        retried = dict(body)
        retried["state"] = "FAILED"
        status = _post_once(client, legacy, retried, auth, timeout_s)
        if _success(status):
            return StatusPost(True, [dict(_CANCEL_WARNING)], attempted=True)
        return StatusPost(False, [dict(_CANCEL_RETRY_FAILED)], attempted=True)
    return StatusPost(False, attempted=True)
