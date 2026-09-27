from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import delete, func, select

from app.api.deps import DB, CurrentUser, require, require_any
from app.core.exceptions import BusinessError
from app.core.permissions import can_manage_access, normalize_permissions
from app.core.security import hash_password, validate_password
from app.models import AuditLog, Role, User
from app.models import Session as UserSession
from app.schemas.domain import (
    ResetPasswordRequest,
    RoleCreate,
    RoleOut,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services.audit import add_audit

router = APIRouter(tags=["系统管理"])


def get_active_user(db: DB, item_id: int) -> User:
    item = db.scalar(
        select(User).where(
            User.id == item_id,
            User.is_deleted.is_(False),
        )
    )
    if not item:
        raise BusinessError("USER_NOT_FOUND", "用户不存在", 404)
    return item


def normalize_role_permissions(permissions: list[str]) -> list[str]:
    try:
        return normalize_permissions(permissions)
    except ValueError as exc:
        raise BusinessError("INVALID_PERMISSIONS", str(exc), 422) from exc


def ensure_access_administrator_remains(
    db: DB,
    *,
    changed_user_id: int | None = None,
    changed_user_active: bool | None = None,
    changed_user_role_id: int | None = None,
    changed_role_id: int | None = None,
    changed_role_permissions: list[str] | None = None,
) -> None:
    candidates = db.scalars(
        select(User).where(
            User.is_active.is_(True),
            User.is_deleted.is_(False),
        )
    ).all()
    for candidate in candidates:
        is_active = candidate.is_active
        role_id = candidate.role_id
        if candidate.id == changed_user_id:
            if changed_user_active is not None:
                is_active = changed_user_active
            if changed_user_role_id is not None:
                role_id = changed_user_role_id
        if not is_active:
            continue
        if role_id == changed_role_id and changed_role_permissions is not None:
            permissions = changed_role_permissions
        elif role_id == candidate.role_id:
            permissions = candidate.role.permissions
        else:
            role = db.get(Role, role_id)
            permissions = role.permissions if role else []
        if can_manage_access(permissions):
            return
    raise BusinessError(
        "LAST_ACCESS_ADMIN",
        "系统必须至少保留一名同时拥有用户管理和角色管理权限的启用用户",
        409,
    )


@router.get("/users", dependencies=[Depends(require("user:manage"))])
def users(db: DB, user: CurrentUser):
    return [
        UserOut.model_validate(x)
        for x in db.scalars(
            select(User)
            .where(User.is_deleted.is_(False))
            .order_by(User.username)
        ).all()
    ]


@router.post(
    "/users",
    response_model=UserOut,
    status_code=201,
    dependencies=[Depends(require("user:manage"))],
)
def create_user(p: UserCreate, request: Request, db: DB, user: CurrentUser):
    if db.scalar(
        select(User.id).where(
            User.username == p.username,
            User.is_deleted.is_(False),
        )
    ):
        raise BusinessError("USERNAME_EXISTS", "登录账号已存在", 409)
    if not db.get(Role, p.role_id):
        raise BusinessError("ROLE_NOT_FOUND", "角色不存在", 404)
    try:
        validate_password(p.password)
    except ValueError as exc:
        raise BusinessError("WEAK_PASSWORD", str(exc)) from exc
    item = User(
        username=p.username,
        full_name=p.full_name,
        department=p.department,
        role_id=p.role_id,
        password_hash=hash_password(p.password),
    )
    db.add(item)
    db.flush()
    add_audit(db, user.id, "user.create", "user", str(item.id), request.state.request_id)
    db.commit()
    return item


@router.put(
    "/users/{item_id}", response_model=UserOut, dependencies=[Depends(require("user:manage"))]
)
def update_user(item_id: int, p: UserUpdate, request: Request, db: DB, user: CurrentUser):
    item = get_active_user(db, item_id)
    changes = p.model_dump(exclude_unset=True)
    if item_id == user.id and changes.get("is_active") is False:
        raise BusinessError("CANNOT_DISABLE_SELF", "不能停用当前登录账号", 409)
    if "role_id" in changes and not db.get(Role, changes["role_id"]):
        raise BusinessError("ROLE_NOT_FOUND", "角色不存在", 404)
    future_active = changes.get("is_active", item.is_active)
    future_role_id = changes.get("role_id", item.role_id)
    if future_active != item.is_active or future_role_id != item.role_id:
        ensure_access_administrator_remains(
            db,
            changed_user_id=item.id,
            changed_user_active=future_active,
            changed_user_role_id=future_role_id,
        )
    before = {
        "full_name": item.full_name,
        "department": item.department,
        "role_id": item.role_id,
        "is_active": item.is_active,
    }
    for k, v in changes.items():
        setattr(item, k, v)
    add_audit(
        db,
        user.id,
        "user.update",
        "user",
        str(item.id),
        request.state.request_id,
        before=before,
        after=changes,
    )
    db.commit()
    return item


@router.delete("/users/{item_id}", dependencies=[Depends(require("user:manage"))])
def delete_user(item_id: int, request: Request, db: DB, user: CurrentUser):
    item = get_active_user(db, item_id)
    if item.id == user.id:
        raise BusinessError("CANNOT_DELETE_SELF", "不能删除当前登录账号", 409)
    ensure_access_administrator_remains(
        db,
        changed_user_id=item.id,
        changed_user_active=False,
    )
    before = {
        "username": item.username,
        "full_name": item.full_name,
        "department": item.department,
        "role_id": item.role_id,
        "is_active": item.is_active,
    }
    db.execute(delete(UserSession).where(UserSession.user_id == item.id))
    item.username = f"deleted-{item.id}-{uuid4().hex[:12]}"
    item.full_name = f"已删除用户 #{item.id}"
    item.department = ""
    item.is_active = False
    item.is_deleted = True
    item.deleted_at = datetime.now(UTC)
    item.must_change_password = False
    item.failed_attempts = 0
    item.locked_until = None
    add_audit(
        db,
        user.id,
        "user.delete",
        "user",
        str(item.id),
        request.state.request_id,
        before=before,
        after={"is_deleted": True},
    )
    db.commit()
    return {"message": "用户已删除，历史业务记录已保留"}


@router.post("/users/{item_id}/reset-password", dependencies=[Depends(require("user:manage"))])
def reset_password(
    item_id: int, p: ResetPasswordRequest, request: Request, db: DB, user: CurrentUser
):
    item = get_active_user(db, item_id)
    try:
        validate_password(p.new_password)
    except ValueError as exc:
        raise BusinessError("WEAK_PASSWORD", str(exc)) from exc
    item.password_hash = hash_password(p.new_password)
    item.must_change_password = True
    add_audit(db, user.id, "user.reset_password", "user", str(item.id), request.state.request_id)
    db.commit()
    return {"message": "密码已重置"}


@router.get(
    "/roles",
    dependencies=[Depends(require_any("user:manage", "role:manage"))],
)
def roles(db: DB, user: CurrentUser):
    return [RoleOut.model_validate(x) for x in db.scalars(select(Role).order_by(Role.id)).all()]


@router.post(
    "/roles",
    response_model=RoleOut,
    status_code=201,
    dependencies=[Depends(require("role:manage"))],
)
def create_role(p: RoleCreate, request: Request, db: DB, user: CurrentUser):
    if db.scalar(select(Role.id).where(Role.name == p.name)):
        raise BusinessError("ROLE_NAME_EXISTS", "角色名称已存在", 409)
    item = Role(
        name=p.name,
        description=p.description,
        permissions=normalize_role_permissions(p.permissions),
    )
    db.add(item)
    db.flush()
    add_audit(
        db,
        user.id,
        "role.create",
        "role",
        str(item.id),
        request.state.request_id,
        after={
            "name": item.name,
            "description": item.description,
            "permissions": item.permissions,
        },
    )
    db.commit()
    return item


@router.put(
    "/roles/{item_id}",
    response_model=RoleOut,
    dependencies=[Depends(require("role:manage"))],
)
def update_role(item_id: int, p: RoleCreate, request: Request, db: DB, user: CurrentUser):
    item = db.get(Role, item_id)
    if not item:
        raise BusinessError("ROLE_NOT_FOUND", "角色不存在", 404)
    duplicate = db.scalar(
        select(Role.id).where(
            Role.name == p.name,
            Role.id != item_id,
        )
    )
    if duplicate:
        raise BusinessError("ROLE_NAME_EXISTS", "角色名称已存在", 409)
    permissions = normalize_role_permissions(p.permissions)
    ensure_access_administrator_remains(
        db,
        changed_role_id=item.id,
        changed_role_permissions=permissions,
    )
    before = {
        "name": item.name,
        "description": item.description,
        "permissions": item.permissions,
    }
    item.name, item.description, item.permissions = p.name, p.description, permissions
    add_audit(
        db,
        user.id,
        "role.update",
        "role",
        str(item.id),
        request.state.request_id,
        before=before,
        after={
            "name": item.name,
            "description": item.description,
            "permissions": item.permissions,
        },
    )
    db.commit()
    return item


@router.get("/audit-logs", dependencies=[Depends(require("audit:view"))])
def audit_logs(
    db: DB, user: CurrentUser, page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=200)
):
    total = db.scalar(select(func.count(AuditLog.id))) or 0
    items = db.scalars(
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    fields = [
        "id",
        "actor_id",
        "action",
        "resource_type",
        "resource_id",
        "request_id",
        "success",
        "ip_address",
        "created_at",
    ]
    return {
        "items": [{k: getattr(x, k) for k in fields} for x in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
