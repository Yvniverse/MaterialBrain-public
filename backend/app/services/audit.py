from sqlalchemy.orm import Session

from app.models import AuditLog


def add_audit(
    db: Session,
    actor_id: int | None,
    action: str,
    resource_type: str,
    resource_id: str,
    request_id: str,
    before=None,
    after=None,
    ip="",
    success=True,
):
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            before_data=before,
            after_data=after,
            ip_address=ip,
            success=success,
        )
    )
