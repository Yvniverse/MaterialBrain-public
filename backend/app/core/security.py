import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return password_hasher.verify(encoded, password)
    except VerifyMismatchError:
        return False


def new_session_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(48)
    return token, hashlib.sha256(token.encode()).hexdigest()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def expires_at() -> datetime:
    return datetime.now(UTC) + timedelta(hours=settings.session_hours)


def validate_password(password: str) -> None:
    if len(password) < 6:
        raise ValueError("密码至少 6 位，可以仅使用数字")
