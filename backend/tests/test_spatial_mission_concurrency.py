"""Real PostgreSQL contention with isolated, independently stale request sessions."""

import copy
import threading
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_spatial_gis_postgis import postgis_engine as _postgis_engine
from test_spatial_integration import (
    NoMotion,
    _execution,
    _ready_preparation,
    _replay_event,
    _started_replay,
    request,
)

from app.agent.conversation import ConversationContextService
from app.agent.spatial_integration import SpatialAgentIntegration
from app.core.config import Settings
from app.core.exceptions import BusinessError
from app.models import AgentConversationContext, InventoryLot, Material, Role, User
from app.services.spatial_mission_store import SpatialMissionStore
from app.spatial import SpatialMapService

postgis_engine = _postgis_engine


@pytest.fixture
def isolated_owner(postgis_engine):
    # The imported fixture refuses production endpoints and non-test database
    # names before connecting/migrating. These rows belong only to this test.
    with Session(postgis_engine) as db:
        suffix = uuid.uuid4().hex
        role = Role(name="p3-race-" + suffix, permissions=["material:view", "project:view"])
        db.add(role)
        db.flush()
        users = [
            User(
                username=f"p3-race-{suffix}-{index}",
                full_name="Isolated concurrency replay",
                password_hash="not-a-login",
                role_id=role.id,
            )
            for index in range(2)
        ]
        db.add_all(users)
        db.commit()
        owner_ids, role_id = [user.id for user in users], role.id
        SpatialMapService(db).register_lab()
        db.commit()
        try:
            yield db, users
        finally:
            db.rollback()
            db.execute(
                delete(AgentConversationContext).where(
                    AgentConversationContext.user_id.in_(owner_ids)
                )
            )
            db.execute(delete(User).where(User.id.in_(owner_ids)))
            db.execute(delete(Role).where(Role.id == role_id))
            db.commit()


def _business(db):
    return (
        list(
            db.execute(
                select(Material.id, Material.quantity, Material.reserved_quantity).order_by(
                    Material.id
                )
            )
        ),
        list(db.execute(select(InventoryLot.id, InventoryLot.quantity).order_by(InventoryLot.id))),
    )


@pytest.mark.parametrize("handoff", [False, True])
@pytest.mark.parametrize(
    "phrase",
    [
        "请重新规划剩余路线，选择最安全路线，保留已完成交接。",
        "当前位置重新规划，改为最快路线，已完成的不要重做。",
        "V4 任务重规划，使用防静电路线，已核验交接保留。",
    ],
)
def test_five_stale_postgres_polls_race_with_one_natural_chinese_replan(
    isolated_owner, monkeypatch, handoff, phrase
):
    db, users = isolated_owner
    before_business = _business(db)
    if handoff:
        service, row, graph, ledger = _started_replay(isolated_owner, phrase)
    else:
        service = SpatialAgentIntegration(db, users[0], transport=NoMotion())
        planned = service.create_mission(
            {**request(db), "goal_ids": ["P-IC", "P-SENSOR", "P-LAB"]},
            operation_id="p3-five-poll-race",
        )
        graph = planned["task_graph"]
        graph["robot_state"]["current_pose"] = graph["robot_state"]["request"]["start_pose"]
        ledger = [
            _replay_event(graph, 1, "started", "P-IC"),
            _replay_event(graph, 2, "feedback", "P-IC"),
            _replay_event(graph, 3, "feedback", "P-IC"),
        ]
        observed = _execution(graph, ledger)
        observed["current_goal_id"] = "P-IC"
        graph = service._accept(graph, observed)
        row = db.get(AgentConversationContext, graph["conversation_id"])
        service._save(row, graph)
    initial_version = row.context_version
    owner_id, row_id = users[0].id, row.id
    keys = {
        node["id"]: node["idempotency_key"]
        for node in graph["nodes"]
        if node["status"] == "SUCCEEDED"
    }
    db.rollback()
    loaded = threading.Barrier(6, timeout=30)
    control_committed = threading.Event()
    # All five requests initially hold the version predating the control save.
    # Then one request wins each simultaneous CAS round. Three remaining polls
    # exhaust the old three-attempt algorithm deterministically.
    rounds = [threading.Barrier(count, timeout=30) for count in (5, 5, 4)]
    counts, misses = Counter(), Counter()
    counter_lock = threading.Lock()
    transport_lock = threading.Lock()
    original_find, original_save = SpatialMissionStore.find, SpatialMissionStore.save
    original_lock = SpatialMissionStore.locked_context
    held, lock_reads = set(), Counter()
    posts = []
    phases = []

    def synchronized_find(store, mission_id):
        result = original_find(store, mission_id)
        loaded.wait()
        return result

    def contended_save(store, candidate_row, candidate):
        name = threading.current_thread().name
        if name == "p3-control":
            result = original_save(store, candidate_row, candidate)
            control_committed.set()
            return result
        with counter_lock:
            counts[name] += 1
            attempt = counts[name]
        if attempt <= len(rounds):
            rounds[attempt - 1].wait()
        try:
            return original_save(store, candidate_row, candidate)
        except BusinessError as exc:
            if exc.code == "AGENT_CONVERSATION_CONFLICT":
                with counter_lock:
                    misses[name] += 1
            raise

    @contextmanager
    def observed_lock(store, candidate_row_id):
        with original_lock(store, candidate_row_id) as locked:
            name = threading.current_thread().name
            with counter_lock:
                held.add(name)
                lock_reads[name] += 1
            try:
                yield locked
            finally:
                with counter_lock:
                    held.remove(name)

    class PublishedTransport:
        def request(self, method, path, body=None):
            assert threading.current_thread().name not in held, (
                "Robot I/O under a database row lock"
            )
            if method == "POST":
                assert path.endswith("/replan")
                phases.append(body["phase"])
                if body["phase"] == "prepare":
                    return _ready_preparation(graph, ledger, body)
                assert body["phase"] == "commit"
                body = body["plan"]
                with transport_lock:
                    posts.append(copy.deepcopy(body))
                    ledger.append(
                        _replay_event(
                            graph,
                            ledger[-1]["sequence"] + 1,
                            "replanning",
                            source="server_semantic_replan",
                            plan=copy.deepcopy(body),
                        )
                    )
            else:
                assert method == "GET"
                assert control_committed.wait(30)
            with transport_lock:
                execution = _execution(graph, ledger)
                execution["current_goal_id"] = "P-SENSOR" if handoff else "P-IC"
            after = int(path.partition("after_sequence=")[2] or 0)
            execution["events"] = [
                event for event in execution["events"] if event["sequence"] > after
            ]
            return execution

    monkeypatch.setattr(SpatialMissionStore, "find", synchronized_find)
    monkeypatch.setattr(SpatialMissionStore, "save", contended_save)
    monkeypatch.setattr(SpatialMissionStore, "locked_context", observed_lock)

    def run(index):
        threading.current_thread().name = "p3-control" if index == 5 else f"p3-poll-{index}"
        with Session(db.get_bind()) as independent:
            owner = independent.get(User, owner_id)
            integration = SpatialAgentIntegration(
                independent, owner, transport=PublishedTransport()
            )
            if index == 5:
                conversation = ConversationContextService(independent, owner, Settings()).open(
                    row_id
                )
                response = integration.query(
                    phrase,
                    conversation=conversation,
                    operation_id="one-control",
                    request_id="p3-race",
                )
                assert response.intent == "spatial_mission" and response.model_call_count == 0
                return response.entities["spatial_mission"]
            return integration.poll(graph["mission_id"])

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(run, index) for index in range(6)]
        failures = [future.exception() for future in futures if future.exception()]
        assert not failures, [
            (type(error).__name__, getattr(error, "code", "")) for error in failures
        ]
        results = [future.result() for future in futures]
    assert len(posts) == 1
    assert phases == ["prepare", "commit"]
    assert len(misses) == 5 and sum(value >= 3 for value in misses.values()) == 3
    assert sum(lock_reads.values()) == 3 and not held
    db.expire_all()
    final_row = db.get(AgentConversationContext, row_id)
    final = final_row.pending_disambiguation["spatial_task"]
    # Three lock fallbacks see the already-applied semantic event and return
    # current truth without another version bump or another robot request.
    assert final_row.context_version == initial_version + 3
    for result in results:
        assert result["mission_id"] == graph["mission_id"]
        assert result["task_graph"]["completed_goal_ids"] == graph["completed_goal_ids"]
        assert result["task_graph"]["robot_state"]["last_event_sequence"] == ledger[-1]["sequence"]
        assert not result["inventory_written"]
    assert final["map_revision"] == graph["map_revision"]
    assert [event["event_id"] for event in final["events"]] == [
        event["event_id"] for event in ledger
    ]
    assert final["robot_state"]["request"]["profile"] == posts[0]["profile"]
    assert final["last_valid_plan"] == posts[0]
    assert keys == {
        node["id"]: node["idempotency_key"] for node in final["nodes"] if node["id"] in keys
    }
    assert _business(db) == before_business


@pytest.mark.parametrize("terminal", [False, True])
def test_locked_reconciliation_keeps_newer_plan_handoffs_overlay_and_terminal_cursor(
    isolated_owner, monkeypatch, terminal
):
    db, users = isolated_owner
    service, row, stale, ledger = _started_replay(isolated_owner, "V4 重规划")
    original = copy.deepcopy(stale)
    before_business = _business(db)

    class LedgerTransport:
        calls = []

        def request(self, method, path, body=None):
            self.calls.append((method, path))
            assert method == "POST"
            if body["phase"] == "prepare":
                return _ready_preparation(stale, ledger, body)
            assert body["phase"] == "commit"
            return _execution(
                stale,
                [
                    *ledger,
                    _replay_event(
                        stale, 7, "replanning", source="server_semantic_replan", plan=body["plan"]
                    ),
                ],
            )

    service.transport = LedgerTransport()
    current = service._replan(copy.deepcopy(stale), profile="safest")
    current_ledger = current["events"][:]
    if terminal:
        current_ledger.append(_replay_event(current, 8, "cancelled", "P-SENSOR"))
    else:
        current_ledger += [
            _replay_event(current, 8, "arrived", "P-SENSOR"),
            _replay_event(current, 9, "scan_verified", "P-SENSOR", scan_code="P-SENSOR"),
            _replay_event(current, 10, "handoff_verified", "P-SENSOR"),
        ]
    observed = _execution(current, current_ledger)
    observed["status"] = "CANCELLED" if terminal else "NAVIGATING"
    current = service._accept(current, observed)
    current["robot_state"].update(overlay_generation=2, overlay_ids={})
    with Session(db.get_bind()) as independent:
        owner = independent.get(User, users[0].id)
        latest = independent.get(AgentConversationContext, row.id)
        SpatialMissionStore(independent, owner).save(latest, current)
        committed_version = latest.context_version
    stale["robot_state"].update(overlay_generation=1, overlay_ids={"removed": "old"})
    attempts = []

    def exhausted_cas(store, candidate_row, candidate):
        attempts.append(candidate["robot_state"]["last_event_sequence"])
        store.db.rollback()
        raise BusinessError("AGENT_CONVERSATION_CONFLICT", "Controlled CAS contention", 409)

    class DelayedTransport:
        calls = []

        def request(self, method, path, body=None):
            self.calls.append((method, path))
            assert method == "GET"
            return _execution(stale, ledger)

    monkeypatch.setattr(SpatialMissionStore, "save", exhausted_cas)
    service.transport = DelayedTransport()
    saved = service._save(row, stale)
    assert len(attempts) == 3
    assert len(service.transport.calls) == 2  # Optimistic rebases only, never under lock.
    assert saved["task_graph"] == current
    assert saved["task_graph"]["last_valid_plan"] != original["last_valid_plan"]
    assert not db.in_transaction()
    with Session(db.get_bind()) as independent:
        persisted = independent.get(AgentConversationContext, row.id)
        assert persisted.context_version == committed_version
        assert persisted.pending_disambiguation["spatial_task"] == current
    assert _business(db) == before_business


def test_postgres_row_lock_releases_on_read_commit_and_exception(isolated_owner):
    from sqlalchemy import text

    db, users = isolated_owner
    service, row, graph, _ = _started_replay(isolated_owner, "V4 重规划")
    row_id = row.id
    db.rollback()

    def immediately_lockable():
        with Session(db.get_bind()) as independent:
            independent.execute(text("SET LOCAL lock_timeout='500ms'"))
            assert independent.scalar(
                select(AgentConversationContext)
                .where(AgentConversationContext.id == row_id)
                .with_for_update(nowait=True)
            )
            independent.rollback()

    # Idempotent read returns without a write; the context manager releases it.
    assert service._save_locked(row_id, graph, graph)["task_graph"] == graph
    assert not db.in_transaction()
    immediately_lockable()
    with service.store.locked_context(row_id) as locked:
        # Prove PostgreSQL actually excludes another writer while the scope is
        # open, rather than merely inspecting a FOR UPDATE source string.
        with Session(db.get_bind()) as competing:
            with pytest.raises(OperationalError) as caught:
                competing.scalar(
                    select(AgentConversationContext)
                    .where(AgentConversationContext.id == row_id)
                    .with_for_update(nowait=True)
                )
            assert caught.value.orig.sqlstate == "55P03"
            competing.rollback()
        service.store.save(locked, graph)
    assert not db.in_transaction()
    immediately_lockable()
    with pytest.raises(RuntimeError, match="abort reconciliation"):
        with service.store.locked_context(row_id):
            raise RuntimeError("abort reconciliation")
    assert not db.in_transaction()
    immediately_lockable()


@pytest.mark.parametrize("changed_key", ["mission_id", "conversation_id", "map_id", "map_revision"])
def test_locked_reconciliation_identity_failure_releases_lock(isolated_owner, changed_key):
    db, _ = isolated_owner
    service, row, graph, _ = _started_replay(isolated_owner, "V4 重规划")
    row_id, version = row.id, row.context_version
    tampered = {**copy.deepcopy(graph), changed_key: "different-identity"}
    with pytest.raises(BusinessError) as caught:
        service._save_locked(row_id, tampered, graph)
    assert caught.value.code == "AGENT_CONVERSATION_CONFLICT"
    assert not db.in_transaction()
    with Session(db.get_bind()) as independent:
        current = independent.scalar(
            select(AgentConversationContext)
            .where(AgentConversationContext.id == row_id)
            .with_for_update(nowait=True)
        )
        assert current.context_version == version
        assert current.pending_disambiguation["spatial_task"] == graph


@pytest.mark.parametrize("denied", ["other-owner", "expired", "closed"])
def test_locked_context_preserves_owner_active_and_ttl_guards(isolated_owner, denied):
    db, users = isolated_owner
    service, row, graph, _ = _started_replay(isolated_owner, "V4 重规划")
    row_id = row.id
    if denied == "other-owner":
        service = SpatialAgentIntegration(db, users[1], transport=NoMotion())
    elif denied == "expired":
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    else:
        row.status = "closed"
        db.commit()
    with pytest.raises(BusinessError) as caught:
        service._save_locked(row_id, graph, graph)
    assert caught.value.code == "AGENT_CONVERSATION_CONFLICT"
    assert not db.in_transaction()
    with Session(db.get_bind()) as independent:
        stored = independent.scalar(
            select(AgentConversationContext)
            .where(AgentConversationContext.id == row_id)
            .with_for_update(nowait=True)
        )
        assert stored.pending_disambiguation["spatial_task"] == graph


def test_locked_reconciliation_rejects_a_newer_cursor_that_loses_verified_handoffs(isolated_owner):
    db, _ = isolated_owner
    service, row, graph, _ = _started_replay(isolated_owner, "V4 重规划")
    row_id, version = row.id, row.context_version
    invalid = copy.deepcopy(graph)
    invalid["completed_goal_ids"] = []
    invalid["robot_state"]["last_event_sequence"] += 1
    with pytest.raises(BusinessError) as caught:
        service._save_locked(row_id, invalid, graph)
    assert caught.value.code == "SPATIAL_INVALID_EXECUTION_EVENT"
    assert not db.in_transaction()
    with Session(db.get_bind()) as independent:
        stored = independent.scalar(
            select(AgentConversationContext)
            .where(AgentConversationContext.id == row_id)
            .with_for_update(nowait=True)
        )
        assert stored.context_version == version
        assert stored.pending_disambiguation["spatial_task"] == graph


@pytest.mark.parametrize("kind", ["feedback", "cancelled"])
def test_locked_save_advances_new_verified_events_then_is_idempotent(isolated_owner, kind):
    db, _ = isolated_owner
    service, row, graph, ledger = _started_replay(isolated_owner, "V4 重规划")
    row_id, version = row.id, row.context_version
    latest = _execution(graph, [*ledger, _replay_event(graph, 7, kind, "P-SENSOR")])
    if kind == "cancelled":
        latest["status"] = "CANCELLED"
    candidate = service._accept(copy.deepcopy(graph), latest)
    candidate["robot_state"].update(overlay_generation=2, overlay_ids={"active": "new"})
    saved = service._save_locked(row_id, candidate, graph)
    assert saved["task_graph"] == candidate
    assert not db.in_transaction()
    with Session(db.get_bind()) as independent:
        current = independent.get(AgentConversationContext, row_id)
        assert current.context_version == version + 1
        assert current.pending_disambiguation["spatial_task"] == candidate
    assert service._save_locked(row_id, candidate, graph)["task_graph"] == candidate
    assert service._save_locked(row_id, graph, graph)["task_graph"] == candidate
    assert not db.in_transaction()
    with Session(db.get_bind()) as independent:
        current = independent.get(AgentConversationContext, row_id)
        assert current.context_version == version + 1
