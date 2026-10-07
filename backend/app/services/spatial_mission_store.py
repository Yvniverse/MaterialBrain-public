"""Mission metadata belongs to the existing owner-scoped Agent conversation."""

import copy
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.core.config import Settings, settings
from app.core.exceptions import BusinessError
from app.models import AgentConversationContext
from app.services.spatial_poll_diagnostics import (
    diagnostic_timer,
    poll_mark,
    poll_phase,
    poll_timed,
)


class SpatialMissionStore:
    def __init__(self, db, user, config: Settings | None = None):
        self.db, self.user = db, user
        config = settings if config is None else config
        self.ttl = timedelta(minutes=config.agent_conversation_ttl_minutes)

    @poll_timed("store_find")
    def find(self, mission_id: str):
        # Bounded by the existing conversation TTL/cleanup policy. A mission ID
        # alone never permits another user's conversation to be accessed.
        with poll_phase("find_sql"):
            rows = self.db.scalars(
                select(AgentConversationContext).where(
                    AgentConversationContext.user_id == self.user.id,
                    AgentConversationContext.status == "active",
                )
            )
        for row in rows:
            graph = (row.pending_disambiguation or {}).get("spatial_task") or {}
            if graph.get("mission_id") == mission_id:
                if row.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
                    raise BusinessError(
                        "AGENT_CONVERSATION_EXPIRED", "任务上下文已过期，请重新建立任务。", 409
                    )
                with poll_phase("find_copy"):
                    value = copy.deepcopy(graph)
                return row, value
        raise BusinessError("SPATIAL_MISSION_NOT_FOUND", "任务不存在或无法访问。", 404)

    @contextmanager
    def locked_context(self, row_id):
        """Serialize only the final context reconciliation, never robot I/O."""
        holding_started = None
        try:
            with poll_phase("lock_acquire"):
                row = self.db.scalar(
                    select(AgentConversationContext)
                    .where(
                        AgentConversationContext.id == row_id,
                        AgentConversationContext.user_id == self.user.id,
                        AgentConversationContext.status == "active",
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            holding_started = diagnostic_timer()
            if row is None or row.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
                raise BusinessError(
                    "AGENT_CONVERSATION_CONFLICT", "任务上下文已变化，请刷新后重试。", 409
                )
            yield row
        finally:
            # save() commits a changed graph. An idempotent read or any exception
            # must also release its row lock before the request returns.
            try:
                if self.db.in_transaction():
                    with poll_phase("lock_rollback"):
                        self.db.rollback()
            finally:
                if holding_started is not None:
                    poll_mark("lock_hold", holding_started)

    @poll_timed("store_save")
    def save(self, row, graph: dict) -> dict:
        value = copy.deepcopy(graph)
        value["conversation_id"] = row.id
        value["events"] = list(value.get("events") or [])[-128:]
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 1024 * 1024:
            raise BusinessError(
                "SPATIAL_CONTEXT_LIMIT", "任务上下文超过限制，请导出回放后建立新任务。", 422
            )
        current = (row.pending_disambiguation or {}).get("spatial_task") or {}
        if current.get("mission_id") == value.get("mission_id") and current.get(
            "robot_state", {}
        ).get("last_event_sequence", -1) > value.get("robot_state", {}).get(
            "last_event_sequence", -1
        ):
            # A map-overlay commit can expire the ORM row while a control
            # request still holds an older graph. Its refreshed context_version
            # would otherwise let that stale graph pass the SQL CAS.
            with poll_phase("save_rollback"):
                self.db.rollback()
            raise BusinessError(
                "AGENT_CONVERSATION_CONFLICT", "任务刚刚收到更新，请刷新后重试。", 409
            )
        pending = {**(row.pending_disambiguation or {}), "spatial_task": value}
        version = row.context_version
        with poll_phase("save_cas_sql"):
            result = self.db.execute(
                update(AgentConversationContext)
                .where(
                    AgentConversationContext.id == row.id,
                    AgentConversationContext.user_id == self.user.id,
                    AgentConversationContext.context_version == version,
                )
                .values(
                    pending_disambiguation=pending,
                    context_version=version + 1,
                    expires_at=datetime.now(UTC) + self.ttl,
                )
            )
        if result.rowcount != 1:
            with poll_phase("save_rollback"):
                self.db.rollback()
            raise BusinessError(
                "AGENT_CONVERSATION_CONFLICT", "任务刚刚收到更新，请刷新后重试。", 409
            )
        with poll_phase("save_commit"):
            self.db.commit()
        with poll_phase("save_refresh"):
            self.db.refresh(row)
        return value
