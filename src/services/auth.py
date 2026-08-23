from datetime import UTC, datetime, timedelta
from functools import wraps
from uuid import UUID

import bcrypt as _bcrypt
from jose import JWTError, jwt

from src.config import settings
from src.database.postgres import db_pool


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── JWT token ──────────────────────────────────────────────────────────


def create_access_token(user_id: UUID, role: str = "user", extra: dict | None = None) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": expire,
        "iat": datetime.now(UTC),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    """Decode JWT and return payload dict, or None on failure."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id = payload.get("sub")
        if not user_id:
            return None
        return {
            "user_id": UUID(user_id),
            "role": payload.get("role", "user"),
        }
    except (JWTError, KeyError, ValueError):
        return None


# ── User operations ────────────────────────────────────────────────────


async def authenticate_user(username: str, password: str) -> dict | None:
    row = await db_pool.fetchrow(
        "SELECT id, username, email, role, password_hash FROM users WHERE username = $1",
        username,
    )
    if not row or not verify_password(password, row["password_hash"]):
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "role": row["role"],
    }


async def get_user_by_id(user_id: UUID) -> dict | None:
    row = await db_pool.fetchrow(
        "SELECT id, username, email, role, is_active, created_at, updated_at FROM users WHERE id = $1",
        str(user_id),
    )
    return dict(row) if row else None


async def register_user(username: str, email: str, password: str, role: str = "user") -> dict:
    import uuid

    user_id = str(uuid.uuid4())
    hashed = hash_password(password)
    await db_pool.execute(
        "INSERT INTO users (id, username, email, password_hash, role) VALUES ($1, $2, $3, $4, $5)",
        user_id,
        username,
        email,
        hashed,
        role,
    )
    row = await db_pool.fetchrow(
        "SELECT id, username, email, role, is_active, created_at, updated_at FROM users WHERE id = $1",
        str(user_id),
    )
    return dict(row)


# ── Role helpers ───────────────────────────────────────────────────────

ROLE_HIERARCHY = {"viewer": 0, "user": 1, "editor": 2, "admin": 3}


def role_at_least(user_role: str, required: str) -> bool:
    return ROLE_HIERARCHY.get(user_role, -1) >= ROLE_HIERARCHY.get(required, 999)


def require_role(required_role: str):
    """Decorator for route handlers — raises 403 if user role < required_role.

    Must be used AFTER Depends(get_current_user) so that `current_user`
    is available as a kwarg.
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, current_user: dict | None = None, **kwargs):
            if not current_user:
                from fastapi import HTTPException

                raise HTTPException(status_code=401, detail="Not authenticated")
            if not role_at_least(current_user.get("role", "user"), required_role):
                from fastapi import HTTPException

                raise HTTPException(status_code=403, detail="Insufficient permissions")
            return await func(*args, current_user=current_user, **kwargs)

        return wrapper

    return decorator
