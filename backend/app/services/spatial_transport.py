"""Bounded backend-to-Nav2 simulation transport on the internal Docker network."""

from urllib.parse import urlsplit

import httpx

from app.core.exceptions import BusinessError
from app.services.spatial_poll_diagnostics import (
    active_poll_diagnostics,
    diagnostic_count,
    diagnostic_timer,
    poll_mark,
    poll_phase,
)

_BRIDGE_REJECTION_CODES = frozenset(
    {
        "UNKNOWN_ACTIVE_MISSION",
        "MISSION_STATE_DOES_NOT_ALLOW_REPLAN",
        "NAV2_ACTION_TERMINAL_UNCONFIRMED",
        "OBSERVED_START_POSE_CHANGED_REPLAN_REQUIRED",
        "COMPLETED_HANDOFFS_MUST_BE_PRESERVED",
        "STALE_OR_UNKNOWN_MAP_REVISION",
        "REPLAN_PREPARATION_IN_PROGRESS",
        "REPLAN_PREPARATION_REQUIRED",
        "REPLAN_PREPARATION_IDENTITY_CHANGED",
        "REPLAN_HOLD_NOT_CONFIRMED",
        "INVALID_REPLAN_PREPARATION",
    }
)


class SpatialRobotTransport:
    def __init__(self, base_url: str):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise ValueError("INVALID_SPATIAL_BRIDGE_URL")
        self.base_url = base_url.rstrip("/")

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        if not path.startswith("/") or ".." in path:
            raise ValueError("INVALID_BRIDGE_PATH")
        try:
            options = {"timeout": httpx.Timeout(5, connect=2), "trust_env": False}
            started = diagnostic_timer()
            headers_at = None

            def observe_headers(response):
                nonlocal headers_at
                headers_at = diagnostic_timer()
                poll_mark("transport_headers", started)

            if active_poll_diagnostics():
                options["event_hooks"] = {"response": [observe_headers]}
            with poll_phase("transport_request"):
                with httpx.Client(**options) as client:
                    response = client.request(method, self.base_url + path, json=body)
                    if active_poll_diagnostics():
                        poll_mark(
                            "transport_body", headers_at, count=diagnostic_count(response.content)
                        )
            poll_mark("transport_http_status", count=response.status_code)
            if response.status_code >= 400:
                # Retain a small typed reason for diagnosis. Never relay an
                # arbitrary remote message, response body or traceback.
                try:
                    with poll_phase("transport_decode"):
                        rejection = response.json()
                except ValueError:
                    rejection = {}
                code = rejection.get("error") if isinstance(rejection, dict) else None
                details = (
                    {"bridge_error_code": code}
                    if isinstance(code, str) and code in _BRIDGE_REJECTION_CODES
                    else {}
                )
                raise BusinessError(
                    "SPATIAL_BRIDGE_REJECTED",
                    "导航仿真拒绝了当前操作，请检查任务状态。",
                    409,
                    details,
                )
            with poll_phase("transport_decode"):
                value = response.json()
            if not isinstance(value, dict):
                raise ValueError("INVALID_BRIDGE_RESPONSE")
            return value
        except (httpx.HTTPError, ValueError) as exc:
            raise BusinessError(
                "SPATIAL_TRANSPORT_PAUSED", "导航仿真暂不可达，已保留已完成站点，请稍后重试。", 503
            ) from exc
