"""
LedgerX - Core Configuration Module

Centralized configuration management using Pydantic Settings.
Supports environment variables and .env files.
"""

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # =========================================================================
    # Application
    # =========================================================================
    APP_NAME: str = "LedgerX"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = False

    # API Configuration
    API_V1_PREFIX: str = "/v1"
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # =========================================================================
    # Security
    # =========================================================================
    SECRET_KEY: str = Field(
        default="CHANGE-ME-IN-PRODUCTION-USE-STRONG-SECRET",
        description="Secret key for JWT signing",
    )
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # API Keys
    API_KEY_HEADER: str = "X-API-Key"

    # =========================================================================
    # Database (PostgreSQL)
    # =========================================================================
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/ledgerx",
        description="PostgreSQL connection URL",
    )
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10
    DATABASE_POOL_TIMEOUT: int = 30
    DATABASE_ECHO: bool = False  # SQL logging

    # Read Replica (optional)
    DATABASE_REPLICA_URL: str | None = None

    # =========================================================================
    # Redis Cache
    # =========================================================================
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0", description="Redis connection URL"
    )
    REDIS_POOL_SIZE: int = 10

    # Cache TTLs (seconds)
    CACHE_TTL_BALANCE: int = 60  # Balance cache
    CACHE_TTL_WALLET: int = 300  # Wallet metadata
    CACHE_TTL_IDEMPOTENCY: int = 86400  # 24 hours

    # =========================================================================
    # Kafka (Event Streaming)
    # =========================================================================
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_CONSUMER_GROUP: str = "ledgerx-workers"

    # Topics
    KAFKA_TOPIC_TRANSACTIONS: str = "ledgerx.transactions"
    KAFKA_TOPIC_EVENTS: str = "ledgerx.events"
    KAFKA_TOPIC_AUDIT: str = "ledgerx.audit"

    # =========================================================================
    # Business Rules
    # =========================================================================
    # Default limits
    DEFAULT_DAILY_LIMIT: float = 10000.00
    DEFAULT_MONTHLY_LIMIT: float = 100000.00

    # Transaction limits
    MIN_TRANSACTION_AMOUNT: float = 0.01
    MAX_TRANSACTION_AMOUNT: float = 1000000.00

    # Hold configuration
    DEFAULT_HOLD_EXPIRY_MINUTES: int = 1440  # 24 hours
    MAX_HOLD_EXPIRY_MINUTES: int = 10080  # 7 days

    # Retry configuration
    MAX_TRANSACTION_RETRIES: int = 3

    # =========================================================================
    # Rate Limiting
    # =========================================================================
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 1000
    RATE_LIMIT_REQUESTS_PER_SECOND: int = 100

    # =========================================================================
    # Observability
    # =========================================================================
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "text"] = "json"

    # Tracing
    JAEGER_AGENT_HOST: str = "localhost"
    JAEGER_AGENT_PORT: int = 6831
    TRACING_ENABLED: bool = True
    TRACING_SAMPLE_RATE: float = 1.0  # 100% in dev, lower in prod

    # Metrics
    METRICS_ENABLED: bool = True
    METRICS_PORT: int = 9090

    # =========================================================================
    # Validators
    # =========================================================================
    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str, info: Any) -> str:
        if info.data.get("ENVIRONMENT") == "production":
            if v == "CHANGE-ME-IN-PRODUCTION-USE-STRONG-SECRET":
                raise ValueError("SECRET_KEY must be changed in production")
            if len(v) < 32:
                raise ValueError(
                    "SECRET_KEY must be at least 32 characters in production"
                )
        return v

    @property
    def database_url_sync(self) -> str:
        """Sync database URL for Alembic migrations."""
        return str(self.DATABASE_URL).replace("+asyncpg", "")

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"


@lru_cache
def get_settings() -> Settings:
    """
    Get cached settings instance.
    Use dependency injection in FastAPI routes.
    """
    return Settings()


# Export singleton for direct imports
settings = get_settings()
