from dataclasses import dataclass
import hashlib
import logging
import unicodedata

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from redis import ConnectionPool, Redis
from redis.exceptions import RedisError

from app.config import settings
from app.exceptions import RateLimitExceededError, RateLimitUnavailableError


logger = logging.getLogger("app.rate_limit")


# INCR, TTL repair, and expiry assignment execute as one server-side operation.
# Fixed windows deliberately remain simple, but can permit a boundary burst of
# nearly twice the configured limit across two adjacent windows.
FIXED_WINDOW_LUA = """
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('TTL', KEYS[1])
if count == 1 or ttl < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
    ttl = redis.call('TTL', KEYS[1])
end
return {count, ttl}
"""


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    count: int
    remaining: int
    retry_after: int


def _stable_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_login_username(username: str) -> str:
    # The submitted credential remains unchanged for authentication. The
    # conservative bucket normalization only prevents case/outer-whitespace
    # variants from creating independent brute-force counters.
    return unicodedata.normalize("NFKC", username).strip().casefold()


def get_client_ip(request: Request) -> str:
    # Proxy headers are intentionally ignored. They may be trusted only after a
    # deployment guarantees that clients cannot bypass a trusted proxy.
    if request.client is None:
        return "unknown"
    return request.client.host


class RateLimiter:
    def __init__(self, client: Redis, key_prefix: str):
        self.client = client
        self.key_prefix = key_prefix.rstrip(":")

    def check_key(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        result = self.client.eval(
            FIXED_WINDOW_LUA,
            1,
            key,
            window_seconds,
        )
        count, ttl = int(result[0]), int(result[1])
        retry_after = max(ttl, 1)
        return RateLimitDecision(
            allowed=count <= limit,
            limit=limit,
            count=count,
            remaining=max(limit - count, 0),
            retry_after=retry_after,
        )

    def check_login(
        self,
        *,
        client_ip: str,
        username: str,
    ) -> RateLimitDecision:
        normalized_username = normalize_login_username(username)
        key = (
            f"{self.key_prefix}:login:ip:{_stable_digest(client_ip)}"
            f":username:{_stable_digest(normalized_username)}"
        )
        return self.check_key(
            key,
            limit=settings.rate_limit_login_limit,
            window_seconds=settings.rate_limit_login_window_seconds,
        )

    def check_registration(self, *, client_ip: str) -> RateLimitDecision:
        key = (
            f"{self.key_prefix}:register:ip:{_stable_digest(client_ip)}"
        )
        return self.check_key(
            key,
            limit=settings.rate_limit_register_limit,
            window_seconds=settings.rate_limit_register_window_seconds,
        )

    def check_write(
        self,
        *,
        user_id: int,
        scope: str,
        workspace_id: int | None,
    ) -> RateLimitDecision:
        key = f"{self.key_prefix}:write:user:{user_id}:scope:{scope}"
        if workspace_id is not None:
            key = f"{key}:workspace:{workspace_id}"
        return self.check_key(
            key,
            limit=settings.rate_limit_write_limit,
            window_seconds=settings.rate_limit_write_window_seconds,
        )


redis_pool = ConnectionPool.from_url(
    settings.redis_url,
    socket_connect_timeout=settings.redis_connect_timeout_seconds,
    socket_timeout=settings.redis_socket_timeout_seconds,
    decode_responses=False,
)
redis_client = Redis(connection_pool=redis_pool)
rate_limiter = RateLimiter(redis_client, settings.rate_limit_key_prefix)


def get_rate_limiter() -> RateLimiter:
    return rate_limiter


def close_rate_limiter() -> None:
    redis_client.close()
    redis_pool.disconnect()


def _raise_if_exceeded(decision: RateLimitDecision) -> None:
    if not decision.allowed:
        raise RateLimitExceededError(
            limit=decision.limit,
            retry_after=decision.retry_after,
        )


def enforce_registration_rate_limit(
    request: Request,
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> None:
    try:
        decision = limiter.check_registration(
            client_ip=get_client_ip(request),
        )
    except RedisError as exc:
        logger.warning(
            "rate_limit_unavailable category=register policy=%s",
            settings.rate_limit_auth_failure_policy,
        )
        raise RateLimitUnavailableError() from exc
    _raise_if_exceeded(decision)


def enforce_login_rate_limit(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> OAuth2PasswordRequestForm:
    try:
        decision = limiter.check_login(
            client_ip=get_client_ip(request),
            username=form_data.username,
        )
    except RedisError as exc:
        logger.warning(
            "rate_limit_unavailable category=login policy=%s",
            settings.rate_limit_auth_failure_policy,
        )
        raise RateLimitUnavailableError() from exc
    _raise_if_exceeded(decision)
    return form_data


def enforce_write_rate_limit(
    *,
    limiter: RateLimiter,
    user_id: int,
    scope: str,
    workspace_id: int | None,
) -> None:
    try:
        decision = limiter.check_write(
            user_id=user_id,
            scope=scope,
            workspace_id=workspace_id,
        )
    except RedisError:
        logger.warning(
            "rate_limit_unavailable category=write policy=%s "
            "scope=%s user_id=%s workspace_id=%s",
            settings.rate_limit_write_failure_policy,
            scope,
            user_id,
            workspace_id,
        )
        return
    _raise_if_exceeded(decision)
