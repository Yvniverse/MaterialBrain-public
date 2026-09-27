import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response
from sqlalchemy import delete, select

from app.api.deps import DB, CurrentUser
from app.core.config import settings
from app.core.exceptions import BusinessError
from app.core.security import (
    expires_at,
    hash_password,
    new_session_token,
    validate_password,
    verify_password,
)
from app.models import AuditLog, User
from app.models import Session as UserSession
from app.schemas.domain import ChangePasswordRequest, LoginRequest, UserOut

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response, db: DB):
    user = db.scalar(
        select(User).where(
            User.username == payload.username,
            User.is_deleted.is_(False),
        )
    )
    now = datetime.now(UTC)
    valid = bool(
        user
        and user.is_active
        and (not user.locked_until or user.locked_until.replace(tzinfo=UTC) <= now)
        and verify_password(payload.password, user.password_hash)
    )
    if not valid:
        if user:
            user.failed_attempts += 1
            if user.failed_attempts >= settings.login_max_attempts:
                user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
                user.failed_attempts = 0
        db.add(
            AuditLog(
                actor_id=user.id if user else None,
                action="auth.login_failed",
                resource_type="user",
                resource_id=str(user.id if user else ""),
                request_id=request.state.request_id,
                ip_address=request.client.host if request.client else "",
                success=False,
            )
        )
        db.commit()
        raise BusinessError("INVALID_CREDENTIALS", "账号或密码错误，或账号暂时锁定", 401)
    user.failed_attempts = 0
    user.locked_until = None
    token, token_hash = new_session_token()
    csrf_token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            token_hash=token_hash, csrf_token=csrf_token, user_id=user.id, expires_at=expires_at()
        )
    )
    db.add(
        AuditLog(
            actor_id=user.id,
            action="auth.login",
            resource_type="user",
            resource_id=str(user.id),
            request_id=request.state.request_id,
            ip_address=request.client.host if request.client else "",
        )
    )
    db.commit()
    response.set_cookie(
        "pengka_session",
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=settings.session_hours * 3600,
        path="/",
    )
    response.set_cookie(
        "pengka_csrf",
        csrf_token,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=settings.session_hours * 3600,
        path="/",
    )
    return {"user": UserOut.model_validate(user), "csrf_token": csrf_token}


@router.post("/logout")
def logout(response: Response, db: DB, user: CurrentUser):
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.commit()
    response.delete_cookie("pengka_session", path="/")
    response.delete_cookie("pengka_csrf", path="/")
    return {"message": "已安全退出"}


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


@router.put("/change-password")
def change_password(payload: ChangePasswordRequest, db: DB, user: CurrentUser):
    if not verify_password(payload.current_password, user.password_hash):
        raise BusinessError("INVALID_CURRENT_PASSWORD", "当前密码不正确")
    try:
        validate_password(payload.new_password)
    except ValueError as exc:
        raise BusinessError("WEAK_PASSWORD", str(exc)) from exc
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.commit()
    return {"message": "密码已修改，请重新登录"}
