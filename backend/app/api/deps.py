from datetime import UTC, datetime
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import hash_token
from app.models import Session as UserSession
from app.models import User

DB = Annotated[Session, Depends(get_db)]


def get_current_user(
    request: Request,
    db: DB,
    pengka_session: Annotated[str | None, Cookie()] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> User:
    if not pengka_session:
        raise HTTPException(401, "请先登录")
    session = db.scalar(
        select(UserSession).where(UserSession.token_hash == hash_token(pengka_session))
    )
    if not session or session.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
        raise HTTPException(401, "会话已过期")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and x_csrf_token != session.csrf_token:
        raise HTTPException(403, "CSRF 校验失败")
    if session.user.is_deleted or not session.user.is_active:
        raise HTTPException(403, "用户已停用或删除")
    return session.user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require(permission: str):
    def dependency(user: CurrentUser) -> User:
        permissions = set(user.role.permissions or [])
        if "*" not in permissions and permission not in permissions:
            raise HTTPException(403, f"缺少权限：{permission}")
        return user

    return dependency


def require_any(*required_permissions: str):
    def dependency(user: CurrentUser) -> User:
        permissions = set(user.role.permissions or [])
        if "*" not in permissions and not permissions.intersection(required_permissions):
            permission_text = " 或 ".join(required_permissions)
            raise HTTPException(403, f"缺少权限：{permission_text}")
        return user

    return dependency
