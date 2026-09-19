from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=30, gt=0)
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_connect_timeout_seconds: float = Field(default=1.0, gt=0, le=30)
    redis_socket_timeout_seconds: float = Field(default=1.0, gt=0, le=30)
    rate_limit_key_prefix: str = Field(
        default="task-api:rate-limit:v1",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9:_-]*$",
    )
    rate_limit_login_limit: int = Field(default=5, gt=0)
    rate_limit_login_window_seconds: int = Field(default=60, gt=0)
    rate_limit_register_limit: int = Field(default=3, gt=0)
    rate_limit_register_window_seconds: int = Field(default=3600, gt=0)
    rate_limit_write_limit: int = Field(default=60, gt=0)
    rate_limit_write_window_seconds: int = Field(default=60, gt=0)
    rate_limit_auth_failure_policy: Literal["closed"] = "closed"
    rate_limit_write_failure_policy: Literal["open"] = "open"

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
            raise ValueError("REDIS_URL must be a redis:// or rediss:// URL")
        return value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
