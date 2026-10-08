"""Real middleware scoping and deterministic, offline HTTP timing/privacy."""

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.agent.spatial_integration import SpatialAgentIntegration
from app.core.exceptions import BusinessError
from app.services import spatial_poll_diagnostics as diagnostics
from app.services.spatial_transport import SpatialRobotTransport

MISSION = "SM-" + "a" * 28
PATH = "/api/v1/spatial/missions/" + MISSION
REQUEST_ID = "c501c4da-b7cb-457d-b229-0a001a627f68"
SECRET = "private-body-header-prompt-must-not-be-logged"


def messages(caplog, event="spatial_poll_finish"):
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == diagnostics.__name__ and json.loads(record.getMessage())["event"] == event
    ]


def assert_safe(value):
    text = json.dumps(value)
    assert SECRET not in text
    assert len(text) < 20000
    assert set(value) == {
        "event",
        "request_id",
        "mission_id",
        "duration_ms",
        "outcome",
        "http_status",
        "phases",
        "phases_dropped",
        "logging_failures",
    }
    assert len(value["phases"]) <= 64
    for phase in value["phases"]:
        assert set(phase) == {"stage", "duration_ms", "count", "outcome"}
        assert phase["stage"] in diagnostics._STAGES
        assert isinstance(phase["duration_ms"], (int, float))
        assert 0 <= phase["duration_ms"] <= 86400000
        assert type(phase["count"]) is int and 0 <= phase["count"] < 2**31
        assert phase["outcome"] in diagnostics._OUTCOMES


def test_actual_middleware_and_route_entry_use_same_safe_request_id(
    client, admin, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger=diagnostics.__name__)
    monkeypatch.setattr(
        SpatialAgentIntegration, "poll", lambda self, mission_id: {"mission_id": mission_id}
    )
    response = client.get(PATH, headers={"X-Request-ID": REQUEST_ID})
    assert response.status_code == 200 and response.json() == {"mission_id": MISSION}
    assert response.headers["X-Request-ID"] == REQUEST_ID
    (start,) = messages(caplog, "spatial_poll_start")
    (finish,) = messages(caplog)
    assert start["request_id"] == finish["request_id"] == REQUEST_ID
    assert finish["mission_id"] == MISSION and finish["outcome"] == "ok"
    assert [phase["stage"] for phase in finish["phases"]] == ["route_entry", "http_dispatch"]
    assert_safe(finish)


@pytest.mark.parametrize(
    "request_id", [SECRET, "a" * 64, "urn:uuid:" + REQUEST_ID, "{" + REQUEST_ID + "}", ""]
)
def test_actual_middleware_invalid_request_id_is_not_logged(
    client, admin, monkeypatch, caplog, request_id
):
    caplog.set_level(logging.INFO, logger=diagnostics.__name__)
    monkeypatch.setattr(
        SpatialAgentIntegration, "poll", lambda self, mission_id: {"mission_id": mission_id}
    )
    response = client.get(PATH, headers={"X-Request-ID": request_id})
    assert response.status_code == 200
    (finish,) = messages(caplog)
    assert str(uuid.UUID(finish["request_id"])) == finish["request_id"]
    assert finish["request_id"] != request_id
    assert_safe(finish)


@pytest.mark.parametrize(
    "path",
    [
        PATH + "/episode",
        PATH + "/start",
        PATH + "/",
        PATH + "x",
        PATH.upper(),
        "/api/v1/materials/" + SECRET,
    ],
)
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_only_exact_lowercase_mission_get_is_scoped(caplog, path, method):
    caplog.set_level(logging.INFO, logger=diagnostics.__name__)
    with diagnostics.poll_diagnostics(method, path, SECRET) as trace:
        assert trace is None
        diagnostics.poll_mark("route_entry")
    assert not messages(caplog) and not diagnostics.active_poll_diagnostics()


def test_unrelated_real_middleware_route_remains_untraced(client, admin, caplog):
    caplog.set_level(logging.INFO, logger=diagnostics.__name__)
    response = client.get("/api/v1/health", headers={"X-Request-ID": SECRET})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == SECRET
    assert not messages(caplog)


def test_actual_httpx_hook_distinguishes_headers_from_complete_body(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=diagnostics.__name__)
    sequence = []
    body = json.dumps({"value": SECRET}).encode()

    class SlowBody(httpx.SyncByteStream):
        def __iter__(self):
            sequence.append("body_read")
            time.sleep(0.012)
            yield body

    def handler(request):
        sequence.append("headers")
        return httpx.Response(200, stream=SlowBody())

    original = httpx.Client
    options_seen = []

    def client_factory(**options):
        options_seen.append(options)
        return original(**options, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("app.services.spatial_transport.httpx.Client", client_factory)
    with diagnostics.poll_diagnostics("GET", PATH, REQUEST_ID):
        value = SpatialRobotTransport("http://bridge.invalid").request(
            "GET", "/missions/" + MISSION
        )
    assert value == {"value": SECRET}
    assert sequence == ["headers", "body_read"]
    assert options_seen[0]["trust_env"] is False
    assert options_seen[0]["timeout"].read == 5 and options_seen[0]["timeout"].connect == 2
    (finish,) = messages(caplog)
    phases = {phase["stage"]: phase for phase in finish["phases"]}
    assert phases["transport_body"]["duration_ms"] >= 10
    assert phases["transport_body"]["count"] == len(body)
    assert phases["transport_http_status"]["count"] == 200
    order = [phase["stage"] for phase in finish["phases"]]
    assert (
        order.index("transport_headers")
        < order.index("transport_body")
        < order.index("transport_decode")
    )
    assert_safe(finish)


@pytest.mark.parametrize(
    "case,expected_status",
    [
        ("invalid_json", 503),
        ("non_dict", 503),
        ("rejected", 409),
        ("nonstring_code", 409),
        ("timeout", 503),
    ],
)
def test_transport_failure_preserves_original_typed_error_and_logs_no_remote_content(
    monkeypatch, caplog, case, expected_status
):
    caplog.set_level(logging.INFO, logger=diagnostics.__name__)

    def handler(request):
        if case == "timeout":
            raise httpx.ReadTimeout(SECRET)
        if case == "invalid_json":
            return httpx.Response(200, content=SECRET.encode())
        if case == "non_dict":
            return httpx.Response(200, json=[SECRET])
        return httpx.Response(
            409,
            json={
                "error": "OBSERVED_START_POSE_CHANGED_REPLAN_REQUIRED"
                if case == "rejected"
                else [SECRET],
                "message": SECRET,
            },
        )

    original = httpx.Client
    monkeypatch.setattr(
        "app.services.spatial_transport.httpx.Client",
        lambda **options: original(**options, transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(BusinessError) as error:
        with diagnostics.poll_diagnostics("GET", PATH, REQUEST_ID):
            SpatialRobotTransport("http://bridge.invalid").request("GET", "/missions/" + MISSION)
    assert error.value.status_code == expected_status
    assert error.value.code == (
        "SPATIAL_BRIDGE_REJECTED" if expected_status == 409 else "SPATIAL_TRANSPORT_PAUSED"
    )
    assert error.value.details == (
        {"bridge_error_code": "OBSERVED_START_POSE_CHANGED_REPLAN_REQUIRED"}
        if case == "rejected"
        else {}
    )
    (finish,) = messages(caplog)
    assert finish["outcome"] != "ok"
    assert_safe(finish)


def test_real_middleware_log_failure_does_not_change_business_error(client, admin, monkeypatch):
    original = BusinessError("SPATIAL_STALE_MAP", SECRET, 409, {"private": SECRET})

    def fail_poll(self, mission_id):
        raise original

    def broken_logger(*args, **kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(SpatialAgentIntegration, "poll", fail_poll)
    monkeypatch.setattr(diagnostics._logger, "info", broken_logger)
    response = client.get(PATH, headers={"X-Request-ID": REQUEST_ID})
    assert response.status_code == 409
    assert response.json()["code"] == original.code
    assert response.json()["message"] == SECRET
    assert response.json()["details"] == original.details


def test_logger_start_failure_is_visible_in_final_summary_without_changing_success(
    client, admin, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger=diagnostics.__name__)
    original = diagnostics._logger.info
    calls = 0

    def once_broken(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError(SECRET)
        return original(*args, **kwargs)

    monkeypatch.setattr(diagnostics._logger, "info", once_broken)
    monkeypatch.setattr(
        SpatialAgentIntegration, "poll", lambda self, mission_id: {"mission_id": mission_id}
    )
    assert client.get(PATH).status_code == 200
    (finish,) = messages(caplog)
    assert finish["logging_failures"] == 1 and finish["outcome"] == "ok"
    assert_safe(finish)


def test_actual_concurrent_middleware_requests_keep_separate_traces(
    client, admin, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger=diagnostics.__name__)
    barrier = threading.Barrier(5, timeout=10)

    def poll(self, mission_id):
        barrier.wait()
        with diagnostics.poll_phase("accept_events", count=int(mission_id[-1], 16)):
            return {"mission_id": mission_id, "request_id": diagnostics._CURRENT.get().request_id}

    monkeypatch.setattr(SpatialAgentIntegration, "poll", poll)
    identities = {"SM-" + format(index, "028x"): str(uuid.uuid4()) for index in range(5)}

    def request(pair):
        mission_id, request_id = pair
        response = client.get(
            "/api/v1/spatial/missions/" + mission_id, headers={"X-Request-ID": request_id}
        )
        assert response.status_code == 200
        assert response.json() == {"mission_id": mission_id, "request_id": request_id}

    with ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(request, identities.items()))
    finishes = messages(caplog)
    assert len(finishes) == 5
    for finish in finishes:
        assert finish["request_id"] == identities[finish["mission_id"]]
        (phase,) = [item for item in finish["phases"] if item["stage"] == "accept_events"]
        assert phase["count"] == int(finish["mission_id"][-1], 16)
        assert_safe(finish)
    assert not diagnostics.active_poll_diagnostics()


def test_phase_budget_rejects_unknown_names_and_unsafe_numeric_values(caplog):
    caplog.set_level(logging.INFO, logger=diagnostics.__name__)
    with diagnostics.poll_diagnostics("GET", PATH, REQUEST_ID) as trace:
        trace.record(SECRET, count=SECRET, outcome=SECRET)
        trace.record("route_entry", count=SECRET, outcome=SECRET)
        for _ in range(100):
            diagnostics.poll_mark("route_entry", count=2**100)
    (finish,) = messages(caplog)
    assert len(finish["phases"]) == 64 and finish["phases_dropped"] == 38
    assert_safe(finish)


def test_original_exception_identity_and_context_restoration(caplog):
    caplog.set_level(logging.INFO, logger=diagnostics.__name__)
    original = BusinessError(SECRET, SECRET, 418)
    with pytest.raises(BusinessError) as raised:
        with diagnostics.poll_diagnostics("GET", PATH, REQUEST_ID):
            with diagnostics.poll_phase("poll_find"):
                raise original
    assert raised.value is original
    (finish,) = messages(caplog)
    assert finish["outcome"] == "business_error"
    assert not diagnostics.active_poll_diagnostics()
    assert_safe(finish)
