from datetime import datetime, timedelta, timezone
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext

from src.config import settings
from src.database.postgres import pg_pool

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: UUID) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> UUID | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return UUID(payload["sub"])
    except (JWTError, KeyError):
        return None


async def authenticate_user(username: str, password: str) -> dict | None:
    row = await pg_pool.fetchrow(
        "SELECT id, username, email, password_hash FROM users WHERE username = $1",
        username,
    )
    if not row or not verify_password(password, row["password_hash"]):
        return None
    return {"id": row["id"], "username": row["username"], "email": row["email"]}


async def get_user_by_id(user_id: UUID) -> dict | None:
    row = await pg_pool.fetchrow(
        "SELECT id, username, email, is_active, created_at, updated_at FROM users WHERE id = $1",
        user_id,
    )
    return dict(row) if row else None


async def register_user(username: str, email: str, password: str) -> dict:
    hashed = hash_password(password)
    row = await pg_pool.fetchrow(
        "INSERT INTO users (username, email, password_hash) VALUES ($1, $2, $3) "
        "RETURNING id, username, email, is_active, created_at, updated_at",
        username,
        email,
        hashed,
    )
    return dict(row)
