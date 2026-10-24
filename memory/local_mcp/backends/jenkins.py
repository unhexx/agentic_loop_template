# -*- coding: utf-8 -*-
"""Trigger one Jenkins job, or supervise an operator-installed Jenkinsfile runner.

No Groovy interpreter, no runner download, and no GitHub Checks call.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from memory.local_mcp import stream as stream_mod
from memory.local_mcp.backends.act import _regular_under
from memory.local_mcp.backends.bitbucket_status import post_commit_status
from memory.local_mcp.probe import (
    _MESSAGES as _PROBE_MESSAGES,
    _NoRedirect,
    _REDIRECTS,
    _basic_header,
    _jenkins_url,
    _origin,
    probe_jenkins,
)
from memory.local_mcp.redact import clip_message
from memory.logutil import get_logger

log = get_logger("memory.local_mcp")

_READ_MAX = 1024 * 1024
# Uncapped /api/json can hide the SCM SHA past this cap. Both readers use it.
_BUILD_TREE = "tree=result,actions[lastBuiltRevision[SHA1,branch[name]],remoteUrls]"
_POLL_S = 0.05
_HEADER_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
_QUEUE_ITEM = re.compile(r"/queue/item/([1-9][0-9]{0,9})(?:/|$)")
# A crumb field with one of these names would replace the auth header or the body.
_HEADER_DENY = frozenset(
    {
        "authorization",
        "host",
        "connection",
        "content-length",
        "content-type",
        "cookie",
        "location",
        "transfer-encoding",
    }
)

_EXTRA = {
    "path_escape": "Choose a path inside the project.",
    "jenkins_crumb_unavailable": (
        "Fix CSRF settings. Do not turn the requirement off by default."
    ),
    "jenkins_controller_required": (
        "Point at a job, or install a local runner binary yourself."
    ),
    "jenkins_queue_truncated": (
        "The build was triggered. The queue item could not be read."
    ),
    "job_failed": "Jenkins job failed",
    "timed_out": "Jenkins build exceeded timeout_s",
    "cancelled": "run cancelled",
}


@dataclass
class JenkinsOutcome:
    exit_code: int
    status: str
    ok: bool
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False
    lines_dropped: int = 0
    log_tail: str = ""
    warnings: list = field(default_factory=list)
    error: dict | None = None
    probe: dict = field(default_factory=dict)
    # Set when GET /api/json failed. The caller returns this object as-is.
    probe_failure: dict | None = None
    posted_by: str | None = None
    scm_commit: str | None = None
    scm_ref: str | None = None


class _Fail(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def _message(code: str) -> str:
    if code in _EXTRA:
        return _EXTRA[code]
    return _PROBE_MESSAGES.get(code, "Refused.")


def _outcome(
    code: str | None,
    *,
    exit_code: int,
    status: str,
    probe: Mapping[str, Any] | None = None,
    log_tail: str = "",
    timed_out: bool = False,
    cancelled: bool = False,
    truncated: bool = False,
    lines_dropped: int = 0,
    probe_failure: dict | None = None,
) -> JenkinsOutcome:
    if probe_failure is not None:
        return JenkinsOutcome(
            exit_code=2,
            status="refused",
            ok=False,
            probe_failure=probe_failure,
        )
    ok = exit_code == 0 and not timed_out and not cancelled and code is None
    if ok:
        status = "succeeded"
        error = None
    else:
        error = {
            "code": code or "job_failed",
            "message": clip_message(_message(code or "job_failed")),
        }
    return JenkinsOutcome(
        exit_code=exit_code,
        status=status,
        ok=ok,
        timed_out=timed_out,
        cancelled=cancelled,
        truncated=truncated,
        lines_dropped=lines_dropped if lines_dropped > 0 else 0,
        log_tail=log_tail,
        error=error,
        probe=dict(probe or {}),
        probe_failure=probe_failure,
    )


def _refused(code: str, probe: Mapping[str, Any] | None = None) -> JenkinsOutcome:
    return _outcome(code, exit_code=2, status="refused", probe=probe)


def _jenkins_map(section: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = section.get("jenkins")
    if isinstance(raw, dict):
        return raw
    return {}


def _crumb_required(section: Mapping[str, Any]) -> bool:
    return _jenkins_map(section).get("crumb_required") is not False


def _report_status(section: Mapping[str, Any]) -> bool:
    return _jenkins_map(section).get("report_status") is True


_STATUS_NOT_POSTED = {
    "code": "bitbucket_status_not_posted",
    "message": "Bitbucket build-status was not posted",
}


def _normalize_commit(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if _COMMIT.fullmatch(text) is None:
        return None
    return text


def _bitbucket_ref(branch: object) -> str | None:
    name: object = None
    if isinstance(branch, str):
        name = branch
    elif isinstance(branch, dict):
        name = branch.get("name")
    elif isinstance(branch, list):
        for item in branch:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                name = item.get("name")
                break
            if isinstance(item, str):
                name = item
                break
    if not isinstance(name, str):
        return None
    text = name.strip()
    if not text or any(ch in text for ch in "\r\n\x00 ?#"):
        return None
    if text.startswith("refs/pull/"):
        return None
    if text.startswith("refs/remotes/origin/"):
        text = "refs/heads/" + text[len("refs/remotes/origin/") :]
    elif text.startswith("refs/remotes/"):
        return None
    elif text.startswith("origin/"):
        text = "refs/heads/" + text[len("origin/") :]
    elif text.startswith("refs/heads/") or text.startswith("refs/tags/"):
        pass
    elif "/" not in text and ".." not in text:
        text = "refs/heads/" + text
    else:
        return None
    if not text.startswith("refs/") or len(text) > 256:
        return None
    return text


def _github_action(action: Mapping[str, Any]) -> bool:
    urls = action.get("remoteUrls")
    if isinstance(urls, list):
        for url in urls:
            if isinstance(url, str) and "github.com" in url.lower():
                return True
    for key in action:
        if isinstance(key, str) and key.lower() == "github":
            return True
    return False


def _revision_from_payload(payload: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """SCM revision on the Jenkins build. A GitHub payload is not a commit."""
    actions = payload.get("actions")
    if not isinstance(actions, list):
        return None, None
    for action in actions:
        if not isinstance(action, dict) or _github_action(action):
            continue
        rev = action.get("lastBuiltRevision")
        if not isinstance(rev, dict):
            continue
        commit = _normalize_commit(rev.get("SHA1"))
        if commit is None:
            continue
        return commit, _bitbucket_ref(rev.get("branch"))
    return None, None


def _status_state(outcome: JenkinsOutcome, jenkins_result: str | None) -> str | None:
    if outcome.cancelled or outcome.timed_out:
        return "CANCELLED"
    if jenkins_result == "SUCCESS" or outcome.ok:
        return "SUCCESSFUL"
    if jenkins_result == "ABORTED":
        return "CANCELLED"
    if jenkins_result in ("FAILURE", "UNSTABLE"):
        return "FAILED"
    if outcome.error and outcome.error.get("code") == "job_failed":
        return "FAILED"
    return None


def _apply_status(
    section: Mapping[str, Any],
    session: "_Session",
    outcome: JenkinsOutcome,
    environ: Mapping[str, str],
    jenkins_result: str | None,
) -> None:
    if session.number is None:
        return
    if session.commit:
        outcome.scm_commit = session.commit
        outcome.scm_ref = session.ref
    if not _report_status(section):
        # The Jenkins Bitbucket plugin posts. This process does not.
        outcome.posted_by = "jenkins"
        return
    state = _status_state(outcome, jenkins_result)
    if state is None or not session.commit:
        return
    build_url = f"{session.base}{_encoded_rest(session.rest)}/{session.number}/"
    try:
        posted = post_commit_status(
            section,
            commit=session.commit,
            state=state,
            key="agentix",
            url=build_url,
            ref=session.ref,
            environ=environ,
        )
    except Exception:
        log.warning("bitbucket status post failed")
        return
    outcome.warnings.extend(posted.warnings)
    if posted.posted:
        outcome.posted_by = "harness"
    elif posted.attempted and not posted.warnings:
        # A rejected or unreachable post stays on the Jenkins exit code.
        outcome.warnings.append(dict(_STATUS_NOT_POSTED))
        log.warning("bitbucket status post rejected")


def rest_path(job_path: str) -> str | None:
    """``folder/name`` becomes ``/job/folder/job/name``. An absolute ``/job/...`` stays.

    Rejects ``..``, a scheme, a host, a query, and anything that is not under ``/job/``.
    """
    if not isinstance(job_path, str) or not job_path or "\x00" in job_path:
        return None
    if any(ch in job_path for ch in "?#\\%\r\n\t"):
        return None
    if "://" in job_path or job_path.startswith("//") or ":" in job_path:
        return None
    parts = job_path.split("/")
    if job_path.startswith("/"):
        segments = parts[1:]
        if len(segments) < 2 or segments[0] != "job":
            return None
    else:
        segments = parts
        if not segments or any(seg in ("", ".", "..") for seg in segments):
            return None
        built: list[str] = []
        for seg in segments:
            built.extend(("job", seg))
        segments = built
    if len(segments) < 2 or any(seg in ("", ".", "..") for seg in segments):
        return None
    if segments[0] != "job":
        return None
    return "/" + "/".join(segments)


def _encoded_rest(rest: str) -> str:
    encoded = []
    for part in rest.split("/"):
        if part == "":
            encoded.append("")
        else:
            encoded.append(urllib.parse.quote(part, safe=""))
    return "/".join(encoded)


def _runner_binary(raw: object) -> Path | None:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        return None
    try:
        resolved = path.resolve()
    except (OSError, ValueError):
        return None
    try:
        mode = resolved.stat().st_mode
    except OSError:
        return None
    if not stat.S_ISREG(mode):
        return None
    if not os.access(resolved, os.X_OK):
        return None
    return resolved


def _runner_env(
    environ: Mapping[str, str],
    extra: Mapping[str, str],
) -> dict[str, str]:
    """Server PATH and locale, then the caller's allowlisted variables. Never HOME."""
    child: dict[str, str] = {}
    for key in ("PATH", "LANG", "USER", "LOGNAME"):
        value = environ.get(key)
        if isinstance(value, str) and value and "\x00" not in value:
            child[key] = value
    for key, value in extra.items():
        # A caller-supplied HOME would point the Jenkinsfile at the operator's ssh dir.
        if key == "HOME" or "\x00" in key or "\x00" in value:
            continue
        child[key] = value
    return child


def _parameter_names(section: Mapping[str, Any]) -> list[str]:
    raw = _jenkins_map(section).get("parameters")
    if not isinstance(raw, list) or not raw:
        return []
    return [item for item in raw if isinstance(item, str) and item]


def _parameter_pairs(
    section: Mapping[str, Any],
    extra: Mapping[str, str],
) -> list[tuple[str, str]] | None:
    """None posts ``/build``. A list, possibly empty, posts ``buildWithParameters``."""
    names = _parameter_names(section)
    if not names:
        return None
    pairs: list[tuple[str, str]] = []
    for name in names:
        if name not in extra:
            continue
        value = extra[name]
        if not isinstance(value, str):
            continue
        pairs.append((name, value))
    return pairs


def _opener() -> urllib.request.OpenerDirector:
    # Empty proxy map: an env proxy would receive the API token.
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )


def _read_body(response: object, *, notice_cut: bool = False) -> tuple[bytes, bool]:
    # One extra byte tells a full 1 MiB body from a truncated 200. Callers that
    # do not care still receive at most _READ_MAX bytes.
    limit = _READ_MAX + 1 if notice_cut else _READ_MAX
    try:
        body = response.read(limit)  # type: ignore[attr-defined]
    except (OSError, http.client.HTTPException):
        return b"", False
    if notice_cut and len(body) > _READ_MAX:
        return body[:_READ_MAX], True
    return body, False


def _queue_item_id(queue_url: str) -> str | None:
    try:
        path = urllib.parse.urlsplit(queue_url).path
    except ValueError:
        return None
    match = _QUEUE_ITEM.search(path)
    if match is None:
        return None
    return match.group(1)


def _queue_api(queue_url: str) -> str:
    # A full queue item can exceed the read cap. Ask only for the build number.
    base = queue_url[:-1] if queue_url.endswith("/") else queue_url
    query = urllib.parse.urlencode({"tree": "executable[number,url]"})
    return base + "/api/json?" + query


def _text_size(headers: Any) -> int | None:
    raw = _header(headers, "X-Text-Size")
    if raw is None:
        return None
    try:
        size = int(raw.strip())
    except ValueError:
        return None
    if size < 0:
        return None
    return size


def _header(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except (AttributeError, TypeError):
        return None
    if isinstance(value, str) and value:
        return value
    return None


def _redirect_code(request_url: str, status: int, headers: Any) -> str | None:
    if status not in _REDIRECTS:
        return None
    location = _header(headers, "Location")
    if not location:
        return "jenkins_unreachable"
    try:
        target = urllib.parse.urljoin(request_url, location)
    except ValueError:
        return "jenkins_redirect_rejected"
    here = _origin(request_url)
    there = _origin(target)
    if here is None or there is None or here != there:
        return "jenkins_redirect_rejected"
    # Same-origin 3xx is still not followed. The controller URL is already pinned.
    return "jenkins_unreachable"


def _json_object(body: bytes) -> dict | None:
    try:
        text = body.decode("utf-8")
    except UnicodeError:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _build_number(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 1 or value > 2_147_483_647:
        return None
    return value


def _safe_crumb(field: object, crumb: object) -> tuple[str, str] | None:
    if not isinstance(field, str) or not isinstance(crumb, str):
        return None
    if not field or not crumb:
        return None
    if _HEADER_TOKEN.fullmatch(field) is None:
        return None
    if field.lower() in _HEADER_DENY:
        return None
    if len(field) > 128 or len(crumb) > 4096:
        return None
    if any(ch in crumb for ch in ("\r", "\n", "\x00")):
        return None
    return field, crumb


class _Session:
    def __init__(
        self,
        base: str,
        origin: tuple[str, str, int],
        auth: str | None,
        deadline: float,
    ) -> None:
        self.base = base
        self.origin = origin
        self.auth = auth
        self.deadline = deadline
        self.opener = _opener()
        self.number: int | None = None
        self.queue_id: str | None = None
        self._body_cut = False
        self.rest = ""
        self.crumb_field: str | None = None
        self.crumb_value: str | None = None
        self.commit: str | None = None
        self.ref: str | None = None
        self.revision_checked = False
        # A progressive range was skipped. Not sink.truncated: that stops later writes.
        self.range_dropped = False

    def _remaining(self) -> float:
        return self.deadline - time.monotonic()

    def _check_time(self, cancel: threading.Event | None) -> None:
        if cancel is not None and cancel.is_set():
            raise _Fail("cancelled")
        if self._remaining() <= 0:
            raise _Fail("timed_out")

    def _pause(self, cancel: threading.Event | None) -> None:
        self._check_time(cancel)
        time.sleep(min(_POLL_S, self._remaining()))
        self._check_time(cancel)

    def request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        crumb: bool = False,
        enforce_deadline: bool = True,
        notice_cut: bool = False,
    ) -> tuple[int, Any, bytes]:
        self._body_cut = False
        parsed = _origin(url)
        if parsed is None or parsed != self.origin:
            raise _Fail("jenkins_redirect_rejected")
        if enforce_deadline:
            self._check_time(None)
            timeout = min(5.0, max(0.1, self._remaining()))
        else:
            # Stop still has to run after the build deadline has already fired.
            timeout = 5.0
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.auth is not None:
            headers["Authorization"] = self.auth
        if crumb and self.crumb_field and self.crumb_value:
            headers[self.crumb_field] = self.crumb_value
        if data is not None and method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                status = int(response.status)
                header_map = response.headers
                body, cut = _read_body(response, notice_cut=notice_cut)
                if cut:
                    self._body_cut = True
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            header_map = exc.headers
            try:
                body = exc.read(_READ_MAX)
            except (OSError, http.client.HTTPException):
                body = b""
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            ValueError,
            UnicodeError,
            http.client.HTTPException,
        ):
            if self._remaining() <= 0:
                raise _Fail("timed_out") from None
            raise _Fail("jenkins_unreachable") from None
        redirected = _redirect_code(url, status, header_map)
        if redirected is not None:
            raise _Fail(redirected)
        return status, header_map, body

    def _stop(self) -> None:
        # Only the build number this call observed. A missing number means we did not start one.
        number = self.number
        if number is None:
            return
        url = f"{self.base}{_encoded_rest(self.rest)}/{number}/stop"
        try:
            self.request("POST", url, data=b"", crumb=True, enforce_deadline=False)
        except _Fail:
            log.warning("jenkins stop failed")

    def _cancel_queue(self) -> None:
        # The queue id from this call's Location. Do not cancel any other item.
        item_id = self.queue_id
        if item_id is None:
            log.warning("jenkins queue cancel skipped")
            return
        url = f"{self.base}/queue/cancelItem?id={item_id}"
        try:
            self.request("POST", url, data=b"", crumb=True, enforce_deadline=False)
        except _Fail:
            log.warning("jenkins queue cancel failed")

    def load_crumb(self, section: Mapping[str, Any]) -> None:
        url = self.base + "/crumbIssuer/api/json"
        status, _headers, body = self.request("GET", url)
        if status == 404:
            if _crumb_required(section):
                raise _Fail("jenkins_crumb_unavailable")
            return
        if status in (401, 403):
            raise _Fail("jenkins_auth")
        if status != 200:
            raise _Fail("jenkins_unreachable")
        payload = _json_object(body)
        if payload is None:
            raise _Fail("jenkins_crumb_unavailable")
        parsed = _safe_crumb(payload.get("crumbRequestField"), payload.get("crumb"))
        if parsed is None:
            raise _Fail("jenkins_crumb_unavailable")
        self.crumb_field, self.crumb_value = parsed

    def trigger(
        self,
        rest: str,
        pairs: list[tuple[str, str]] | None,
        cancel: threading.Event | None,
    ) -> str:
        self._check_time(cancel)
        self.rest = rest
        leaf = "build" if pairs is None else "buildWithParameters"
        url = f"{self.base}{_encoded_rest(rest)}/{leaf}"
        data = b""
        if pairs:
            data = urllib.parse.urlencode(pairs).encode("utf-8")
        status, headers, _body = self.request("POST", url, data=data, crumb=True)
        if status in (401, 403):
            raise _Fail("jenkins_auth")
        if status != 201:
            raise _Fail("jenkins_unreachable")
        location = _header(headers, "Location")
        if not location:
            raise _Fail("jenkins_unreachable")
        try:
            target = urllib.parse.urljoin(url, location)
        except ValueError:
            raise _Fail("jenkins_redirect_rejected") from None
        if _origin(target) != self.origin:
            raise _Fail("jenkins_redirect_rejected")
        self.queue_id = _queue_item_id(target)
        return target

    def poll_queue(self, queue_url: str, cancel: threading.Event | None) -> int:
        api = _queue_api(queue_url)
        while True:
            self._check_time(cancel)
            status, _headers, body = self.request("GET", api, notice_cut=True)
            if status in (401, 403):
                raise _Fail("jenkins_auth")
            if status != 200:
                raise _Fail("jenkins_unreachable")
            # A truncated 200 is not a down controller. Do not parse the prefix.
            if self._body_cut:
                raise _Fail("jenkins_queue_truncated")
            payload = _json_object(body)
            if payload is None:
                raise _Fail("jenkins_unreachable")
            executable = payload.get("executable")
            if isinstance(executable, dict):
                number = _build_number(executable.get("number"))
                if number is not None:
                    exec_url = executable.get("url")
                    if isinstance(exec_url, str) and exec_url:
                        if _origin(exec_url) != self.origin:
                            raise _Fail("jenkins_redirect_rejected")
                    self.number = number
                    return number
            self._pause(cancel)

    def _next_start(self, start: int, body: bytes, headers: Any) -> int:
        # A read that hit the cap is not the whole offered range. Jumping to
        # X-Text-Size would skip bytes still in the log.
        cut = len(body) >= _READ_MAX
        size = _text_size(headers)
        if not cut and size is not None and size > start:
            return size
        if body:
            return start + len(body)
        return start

    def tail(
        self,
        sink: stream_mod._Sink,
        cancel: threading.Event | None,
    ) -> None:
        number = self.number
        if number is None:
            return
        start = 0
        pending = ""
        while True:
            self._check_time(cancel)
            url = (
                f"{self.base}{_encoded_rest(self.rest)}/{number}"
                f"/logText/progressiveText?start={start}"
            )
            status, headers, body = self.request("GET", url)
            if status in (401, 403):
                raise _Fail("jenkins_auth")
            if status != 200:
                raise _Fail("jenkins_unreachable")
            text = body.decode("utf-8", errors="replace")
            pending = _feed(sink, pending, text)
            cut = len(body) >= _READ_MAX
            size = _text_size(headers)
            nxt = self._next_start(start, body, headers)
            # X-Text-Size moved past the bytes this response kept.
            if nxt > start + len(body):
                self.range_dropped = True
            more = _header(headers, "X-More-Data")
            more_data = more is not None and more.strip().lower() == "true"
            behind = size is not None and nxt < size
            if (cut or behind) and nxt > start:
                start = nxt
                continue
            start = nxt
            if not more_data:
                if behind:
                    self.range_dropped = True
                break
            self._pause(cancel)
        if pending:
            sink.add(pending)
        sink.flush_progress(wait=True)

    def read_result(self, cancel: threading.Event | None) -> str | None:
        number = self.number
        if number is None:
            return None
        # tree=result alone omits the SCM revision the optional poster reads.
        url = f"{self.base}{_encoded_rest(self.rest)}/{number}/api/json?{_BUILD_TREE}"
        while True:
            self._check_time(cancel)
            status, _headers, body = self.request("GET", url)
            if status in (401, 403):
                raise _Fail("jenkins_auth")
            if status != 200:
                raise _Fail("jenkins_unreachable")
            payload = _json_object(body)
            if payload is None:
                raise _Fail("jenkins_unreachable")
            self._take_revision(payload)
            result = payload.get("result")
            if isinstance(result, str) and result.strip():
                return result.strip()
            self._pause(cancel)

    def _take_revision(self, payload: Mapping[str, Any]) -> None:
        if self.commit is None:
            commit, ref = _revision_from_payload(payload)
            if commit is not None:
                self.commit = commit
                self.ref = ref
        result = payload.get("result")
        if self.commit is not None or (isinstance(result, str) and result.strip()):
            self.revision_checked = True

    def peek_revision(self) -> None:
        number = self.number
        if number is None or self.revision_checked:
            return
        url = f"{self.base}{_encoded_rest(self.rest)}/{number}/api/json?{_BUILD_TREE}"
        status, _headers, body = self.request(
            "GET", url, enforce_deadline=False, notice_cut=True
        )
        if status != 200:
            self.revision_checked = True
            return
        # A cut body is not "no SHA". Leave revision_checked false.
        if self._body_cut:
            return
        payload = _json_object(body)
        if payload is None:
            self.revision_checked = True
            return
        self._take_revision(payload)


def _feed(sink: stream_mod._Sink, pending: str, text: str) -> str:
    pending += text
    while "\n" in pending:
        line, pending = pending.split("\n", 1)
        sink.add(line + "\n")
    if len(pending) >= stream_mod._LINE_MAX:
        sink.add(pending[: stream_mod._LINE_MAX] + "\n")
        pending = pending[stream_mod._LINE_MAX :]
    return pending


def _run_file(
    workdir: Path,
    section: Mapping[str, Any],
    *,
    job: Mapping[str, str],
    extra_env: Mapping[str, str],
    log_path: Path,
    timeout_s: int,
    progress: Callable[..., None] | None,
    protocol: str,
    cancel: threading.Event | None,
    environ: Mapping[str, str],
) -> JenkinsOutcome:
    if cancel is not None and cancel.is_set():
        return _outcome("cancelled", exit_code=130, status="cancelled", cancelled=True)
    resolved = _regular_under(job["jenkinsfile_path"], workdir, workdir)
    if resolved is None:
        return _refused("path_escape")
    binary = _runner_binary(_jenkins_map(section).get("local_runner"))
    if binary is None:
        return _refused("jenkins_controller_required")
    # PATH and loader keys are rejected before this call. HOME is still dropped.
    child = _runner_env(environ, extra_env)
    streamed = stream_mod.stream_cli(
        [str(binary), str(resolved)],
        cwd=workdir,
        env=child,
        log_path=log_path,
        timeout_s=timeout_s,
        cancel=cancel,
        progress=progress,
        protocol=protocol,
    )
    common = {
        "log_tail": streamed.log_tail,
        "truncated": streamed.truncated,
        "lines_dropped": streamed.lines_dropped,
    }
    if streamed.timed_out:
        return _outcome(
            "timed_out",
            exit_code=124,
            status="timed_out",
            timed_out=True,
            **common,
        )
    if streamed.cancelled:
        return _outcome(
            "cancelled",
            exit_code=130,
            status="cancelled",
            cancelled=True,
            **common,
        )
    if streamed.missing:
        return _refused("jenkins_controller_required")
    if streamed.returncode == 0:
        return _outcome(None, exit_code=0, status="succeeded", **common)
    return _outcome("job_failed", exit_code=1, status="failed", **common)


def _log_truncated(sink: stream_mod._Sink | None, session: _Session) -> bool:
    # range_dropped is a skipped progressive offset. sink.truncated is the file cap.
    # Setting the sink flag early would drop the bytes that follow the hole.
    return session.range_dropped or (sink is not None and sink.truncated)


def _map_result(name: str | None, sink: stream_mod._Sink, probe: Mapping[str, Any]) -> JenkinsOutcome:
    common = {
        "probe": probe,
        "log_tail": sink.tail(),
        "truncated": sink.truncated,
        "lines_dropped": sink.lines_dropped,
    }
    if name == "SUCCESS":
        return _outcome(None, exit_code=0, status="succeeded", **common)
    # Cancel and timeout return before this read. A controller ABORTED is a failed job.
    return _outcome("job_failed", exit_code=1, status="failed", **common)


def _run_job(
    workdir: Path,
    section: Mapping[str, Any],
    *,
    job: Mapping[str, str],
    extra_env: Mapping[str, str],
    log_path: Path,
    deadline: float,
    progress: Callable[..., None] | None,
    protocol: str,
    cancel: threading.Event | None,
    environ: Mapping[str, str],
) -> JenkinsOutcome:
    if cancel is not None and cancel.is_set():
        return _outcome("cancelled", exit_code=130, status="cancelled", cancelled=True)
    rest = rest_path(job["job_path"])
    if rest is None:
        return _refused("path_escape")
    probed = probe_jenkins(workdir, timeout_s=min(5.0, max(0.1, deadline - time.monotonic())), environ=environ)
    if not probed.get("ok"):
        return _outcome(
            None,
            exit_code=2,
            status="refused",
            probe_failure=probed,
        )
    probe_body = probed.get("probe")
    if not isinstance(probe_body, dict):
        probe_body = {"jenkins": True}
    base = _jenkins_url(section, environ)
    origin = _origin(base) if isinstance(base, str) else None
    if not isinstance(base, str) or origin is None:
        return _refused("jenkins_unreachable", probe_body)
    # Basic header is computed here and never copied into the result or the log line.
    auth = _basic_header(environ)
    session = _Session(base, origin, auth, deadline)
    sink: stream_mod._Sink | None = None
    log_fp = None
    outcome: JenkinsOutcome | None = None
    result_name: str | None = None
    try:
        session.load_crumb(section)
        if cancel is not None and cancel.is_set():
            raise _Fail("cancelled")
        pairs = _parameter_pairs(section, extra_env)
        queue_url = session.trigger(rest, pairs, cancel)
        session.poll_queue(queue_url, cancel)
        log_fp = stream_mod._open_log(log_path)
        sink = stream_mod._Sink(log_fp, progress, protocol)
        session.tail(sink, cancel)
        result_name = session.read_result(cancel)
        outcome = _map_result(result_name, sink, probe_body)
        if session.range_dropped:
            outcome.truncated = True
    except _Fail as exc:
        if exc.code == "timed_out":
            session._stop()
            outcome = _outcome(
                "timed_out",
                exit_code=124,
                status="timed_out",
                probe=probe_body,
                log_tail="" if sink is None else sink.tail(),
                timed_out=True,
                truncated=_log_truncated(sink, session),
                lines_dropped=0 if sink is None else sink.lines_dropped,
            )
        elif exc.code == "cancelled":
            session._stop()
            outcome = _outcome(
                "cancelled",
                exit_code=130,
                status="cancelled",
                probe=probe_body,
                log_tail="" if sink is None else sink.tail(),
                cancelled=True,
                truncated=_log_truncated(sink, session),
                lines_dropped=0 if sink is None else sink.lines_dropped,
            )
        elif exc.code == "jenkins_queue_truncated":
            # POST /build already returned 201. Cancel the item this call queued.
            session._cancel_queue()
            outcome = _refused(exc.code, probe_body)
        else:
            outcome = _refused(exc.code, probe_body)
    finally:
        if outcome is not None and _report_status(section) and not session.revision_checked:
            try:
                session.peek_revision()
            except _Fail:
                pass
        if outcome is not None:
            try:
                _apply_status(section, session, outcome, environ, result_name)
            except Exception:
                log.warning("bitbucket status post failed")
        if log_fp is not None:
            try:
                log_fp.close()
            except OSError:
                pass
    if outcome is None:
        return _refused("jenkins_unreachable", probe_body)
    return outcome


def trigger_job(
    section: Mapping[str, Any],
    *,
    environ: Mapping[str, str],
    timeout_s: float = 15.0,
) -> str | None:
    """POST the configured ``job_path`` once, with a crumb. No queue poll and no retry."""
    raw_path = _jenkins_map(section).get("job_path")
    if not isinstance(raw_path, str):
        return "path_escape"
    rest = rest_path(raw_path)
    if rest is None:
        return "path_escape"
    base = _jenkins_url(section, environ)
    origin = _origin(base) if isinstance(base, str) else None
    if not isinstance(base, str) or origin is None:
        return "jenkins_unreachable"
    auth = _basic_header(environ)
    try:
        limit = float(timeout_s)
    except (TypeError, ValueError):
        limit = 15.0
    if limit <= 0:
        limit = 15.0
    session = _Session(base, origin, auth, time.monotonic() + limit)
    try:
        session.load_crumb(section)
        # The relay has no parameter values. An empty allowlist match is /build,
        # not buildWithParameters. The pipeline path still passes an empty list.
        pairs = _parameter_pairs(section, {})
        if not pairs:
            pairs = None
        session.trigger(rest, pairs, None)
    except _Fail as exc:
        return exc.code
    return None


def run(
    workdir: Path,
    section: Mapping[str, Any],
    *,
    timeout_s: int,
    log_path: Path,
    job: Mapping[str, str],
    extra_env: Mapping[str, str],
    progress: Callable[..., None] | None,
    protocol: str,
    cancel: threading.Event | None,
    environ: Mapping[str, str],
) -> JenkinsOutcome:
    """``job`` is exactly one of ``job_path`` or ``jenkinsfile_path``."""
    deadline = time.monotonic() + float(timeout_s)
    if "jenkinsfile_path" in job:
        return _run_file(
            workdir,
            section,
            job=job,
            extra_env=extra_env,
            log_path=log_path,
            timeout_s=timeout_s,
            progress=progress,
            protocol=protocol,
            cancel=cancel,
            environ=environ,
        )
    return _run_job(
        workdir,
        section,
        job=job,
        extra_env=extra_env,
        log_path=log_path,
        deadline=deadline,
        progress=progress,
        protocol=protocol,
        cancel=cancel,
        environ=environ,
    )
