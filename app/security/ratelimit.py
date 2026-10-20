"""Fixed-window rate limits for the endpoints that can be abused (WTA-30).

Login and password-reset endpoints take a secret or send an email: without a limit, an
attacker can try passwords all day or make us send thousands of messages. Each limit is a
counter in Redis (`INCR` + `EXPIRE`, atomic in a pipeline) keyed by scope and subject (an
IP, an email, a user id).

- Redis is the shared store because the API may run several containers (ADR-03).
- If Redis is unreachable the limiter **fails open** and logs: a Redis hiccup must not lock
  every user out. The queue needs Redis anyway, so a real outage is noticed elsewhere.
- Tests use `MemoryBackend` (set by conftest), never a live Redis.
"""
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from redis.exceptions import RedisError

from config.variables import settings


@dataclass(frozen=True)
class Limit:
    scope: str  # e.g. "login:ip"
    max_hits: int
    window_seconds: int


class RedisBackend:
    def __init__(self, client):
        self._client = client

    def hit(self, key: str, window: int) -> tuple[int, int]:
        """Increment `key`, return (hits in window, seconds until the window resets)."""
        pipe = self._client.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        hits, ttl = pipe.execute()
        if ttl < 0:  # first hit of the window (or a key that lost its TTL)
            self._client.expire(key, window)
            ttl = window
        return int(hits), int(ttl)


class MemoryBackend:
    """Single-process stand-in for tests and for running without Redis."""

    def __init__(self):
        self._data: dict[str, tuple[int, float]] = {}

    def hit(self, key: str, window: int) -> tuple[int, int]:
        now = time.monotonic()
        hits, reset_at = self._data.get(key, (0, now + window))
        if now >= reset_at:
            hits, reset_at = 0, now + window
        hits += 1
        self._data[key] = (hits, reset_at)
        return hits, max(1, int(reset_at - now))

    def clear(self) -> None:
        self._data.clear()


class RateLimiter:
    def __init__(self, backend):
        self.backend = backend

    def enforce(self, limit: Limit, subject: str) -> None:
        """Count one hit for `subject` under `limit`; raise 429 with Retry-After past the limit."""
        if not settings.rate_limit_enabled or not subject:
            return
        key = f"ratelimit:{limit.scope}:{subject.lower()}"
        try:
            hits, retry_after = self.backend.hit(key, limit.window_seconds)
        except RedisError as exc:
            print(f"[ratelimit] Redis unavailable, allowing request: {exc}", flush=True)
            return
        if hits > limit.max_hits:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many attempts. Try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)},
            )


def client_ip(request: Request) -> str:
    """The caller's address. Behind Caddy, uvicorn rewrites it from X-Forwarded-For
    (`--proxy-headers`, docker-compose.yml), so `request.client.host` is the real one."""
    return request.client.host if request.client else "unknown"


def _default_backend():
    from db.redis.redis_conn import redis_client

    return RedisBackend(redis_client)


limiter = RateLimiter(_default_backend())

# The limits themselves. Per IP they stop one machine hammering us; per email/user they stop
# a distributed attack on one account (and a flood of emails to one inbox).
LOGIN_IP = Limit("login:ip", 20, 15 * 60)
LOGIN_EMAIL = Limit("login:email", 10, 15 * 60)
REGISTER_IP = Limit("register:ip", 10, 60 * 60)
FORGOT_IP = Limit("forgot:ip", 20, 60 * 60)
FORGOT_EMAIL = Limit("forgot:email", 5, 60 * 60)
VERIFY_REQUEST_USER = Limit("verify-request:user", 5, 60 * 60)
TOKEN_IP = Limit("token:ip", 30, 60 * 60)  # guessing verify / reset tokens
