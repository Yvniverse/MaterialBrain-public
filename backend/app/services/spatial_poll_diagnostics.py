"""Bounded, content-free timing of authenticated mission polling dispatches."""

import json
import logging
import math
import re
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from app.core.exceptions import BusinessError

_logger = logging.getLogger(__name__)
_MISSION_PATH = re.compile(r"/api/v1/spatial/missions/(SM-[0-9a-f]{28})")
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_MAX_PHASES = 64
_STAGES = frozenset(
    {
        "http_dispatch",
        "route_entry",
        "poll_permission",
        "poll_total",
        "poll_find",
        "poll_map",
        "poll_transport",
        "digest_before",
        "accept_events",
        "digest_after",
        "integration_save",
        "save_validate",
        "save_cas_conflict",
        "save_rebase_read",
        "save_rebase_accept",
        "locked_reconcile",
        "locked_validate",
        "store_find",
        "find_sql",
        "find_copy",
        "store_save",
        "save_cas_sql",
        "save_commit",
        "save_refresh",
        "save_rollback",
        "lock_acquire",
        "lock_hold",
        "lock_rollback",
        "transport_request",
        "transport_headers",
        "transport_body",
        "transport_http_status",
        "transport_decode",
    }
)
_ERROR_OUTCOMES = {
    "AGENT_CONVERSATION_CONFLICT": "cas_conflict",
    "AGENT_CONVERSATION_EXPIRED": "expired",
    "SPATIAL_MISSION_NOT_FOUND": "not_found",
    "SPATIAL_PERMISSION_REQUIRED": "permission_denied",
    "SPATIAL_STALE_MAP": "stale_map",
    "SPATIAL_TRANSPORT_PAUSED": "transport_paused",
    "SPATIAL_BRIDGE_REJECTED": "bridge_rejected",
    "SPATIAL_CONTEXT_LIMIT": "context_limit",
    "SPATIAL_INVALID_EXECUTION_EVENT": "invalid_event",
    "SPATIAL_EXECUTION_MISMATCH": "invalid_event",
    "SPATIAL_UNREGISTERED_FEEDBACK": "invalid_event",
}
_OUTCOMES = frozenset(_ERROR_OUTCOMES.values()) | {
    "ok",
    "business_error",
    "error",
    "cancelled",
    "http_error",
}
_CURRENT = ContextVar("spatial_poll_diagnostics", default=None)


def _outcome(error):
    if isinstance(error, BusinessError):
        code = error.code
        return (
            _ERROR_OUTCOMES.get(code, "business_error")
            if isinstance(code, str)
            else "business_error"
        )
    return "error" if isinstance(error, Exception) else "cancelled"


def diagnostic_timer():
    """A diagnostic clock failure must never replace a business result."""
    try:
        return time.perf_counter() if _CURRENT.get() is not None else None
    except Exception:
        return None


def diagnostic_count(value):
    return min(len(value), 2**31 - 1) if type(value) in {list, tuple, bytes} else 0


class PollDiagnostics:
    def __init__(self, mission_id, request_id):
        self.mission_id = mission_id
        self.request_id = (
            str(uuid.UUID(request_id))
            if isinstance(request_id, str) and _UUID.fullmatch(request_id)
            else str(uuid.uuid4())
        )
        self.started = time.perf_counter()
        self.phases = []
        self.dropped = 0
        self.logging_failures = 0
        self.http_status = 0
        self.outcome = "ok"
        self.lock = threading.Lock()

    def emit(self, event):
        try:
            if event not in {"spatial_poll_start", "spatial_poll_finish"}:
                return
            payload = {
                "event": event,
                "request_id": self.request_id,
                "mission_id": self.mission_id,
            }
            if event == "spatial_poll_finish":
                payload.update(
                    duration_ms=round((time.perf_counter() - self.started) * 1000, 3),
                    outcome=self.outcome,
                    http_status=self.http_status,
                    phases=self.phases,
                    phases_dropped=self.dropped,
                    logging_failures=self.logging_failures,
                )
            _logger.info(json.dumps(payload, separators=(",", ":"), allow_nan=False))
        except Exception:
            self.logging_failures += 1

    def record(self, stage, started=None, *, count=0, outcome="ok"):
        try:
            with self.lock:
                if stage not in _STAGES or len(self.phases) >= _MAX_PHASES:
                    self.dropped += 1
                    return
                duration = (time.perf_counter() - (started or self.started)) * 1000
                self.phases.append(
                    {
                        "stage": stage,
                        "duration_ms": round(max(0, min(duration, 86400000)), 3)
                        if math.isfinite(duration)
                        else 0,
                        "count": min(max(count, 0), 2**31 - 1) if type(count) is int else 0,
                        "outcome": outcome
                        if isinstance(outcome, str) and outcome in _OUTCOMES
                        else "error",
                    }
                )
        except Exception:
            self.dropped += 1

    def response(self, status):
        if type(status) is int and 100 <= status <= 599:
            self.http_status = status
            if status >= 400 and self.outcome == "ok":
                self.outcome = "http_error"


def active_poll_diagnostics():
    return _CURRENT.get() is not None


def poll_mark(stage, started=None, *, count=0):
    trace = _CURRENT.get()
    if trace is not None:
        trace.record(stage, started, count=count)


@contextmanager
def poll_phase(stage, *, count=0):
    trace = _CURRENT.get()
    if trace is None:
        yield
        return
    started = diagnostic_timer()
    outcome = "ok"
    try:
        yield
    except BaseException as error:
        outcome = _outcome(error)
        raise
    finally:
        trace.record(stage, started, count=count, outcome=outcome)


def poll_timed(stage):
    def decorate(function):
        @wraps(function)
        def measured(*args, **kwargs):
            with poll_phase(stage):
                return function(*args, **kwargs)

        return measured

    return decorate


@contextmanager
def poll_diagnostics(method, path, request_id):
    match = _MISSION_PATH.fullmatch(path) if method == "GET" and isinstance(path, str) else None
    try:
        trace = PollDiagnostics(match[1], request_id) if match else None
    except Exception:
        trace = None
    token = _CURRENT.set(trace)
    try:
        if trace is not None:
            trace.emit("spatial_poll_start")
        try:
            yield trace
        except BaseException as error:
            if trace is not None:
                trace.outcome = _outcome(error)
            raise
        finally:
            if trace is not None:
                trace.emit("spatial_poll_finish")
    finally:
        _CURRENT.reset(token)
