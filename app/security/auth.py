"""Passwords and JWT access tokens (WTA-26).

- Passwords are hashed with argon2id (`pwdlib`, the hash FastAPI's docs recommend); the
  hash carries its own parameters, so they can be raised later without a migration.
- An access token is a signed JWT (HS256, `AUTH_SECRET_KEY`) with the user id as `sub` and
  an expiry. There is no server-side session: a token is valid until it expires, and
  rotating the key invalidates every token at once.
- `get_current_user` is the FastAPI dependency every private route uses. It answers 401 with
  `WWW-Authenticate: Bearer` for a missing, malformed, expired or wrongly signed token and
  for a deleted or deactivated account.
"""
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from config.variables import settings
from db import db_crud
from db.conn import get_db
from db.models_db.models_db import User

ALGORITHM = "HS256"
# Keys this short are guessable; the placeholder in .env.example is caught by main.py too
MIN_SECRET_LENGTH = 32

_hasher = PasswordHash.recommended()
# Verified against when the email is unknown, so a login attempt takes the same time whether
# the account exists or not (no user enumeration through timing)
_DUMMY_HASH = _hasher.hash("not-a-real-password")

# auto_error=False: a missing header gets our own 401 (with WWW-Authenticate) instead of 403
_bearer = HTTPBearer(auto_error=False, description="Paste the access_token from POST /auth/login")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    return _hasher.verify(password, password_hash or _DUMMY_HASH)


def create_access_token(user_id: uuid.UUID, expires_minutes: int | None = None) -> str:
    now = datetime.now(timezone.utc)
    minutes = settings.auth_token_expire_minutes if expires_minutes is None else expires_minutes
    claims = {"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(claims, settings.auth_secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> uuid.UUID | None:
    """The user id in a valid token, or None (expired, bad signature, not ours)."""
    try:
        claims = jwt.decode(token, settings.auth_secret_key, algorithms=[ALGORITHM])
        return uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency: the account behind the `Authorization: Bearer <token>` header."""
    if credentials is None:
        raise _unauthorized("Not authenticated: send an Authorization: Bearer <token> header.")
    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise _unauthorized("Invalid or expired token: log in again.")
    user = db_crud.get_user(db, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("This account no longer exists or is disabled.")
    return user
