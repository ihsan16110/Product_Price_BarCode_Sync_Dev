from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Central DB connection (Head Office — outlet data source)
    SOURCE_SERVER: str
    SOURCE_DATABASE: str
    SOURCE_USER: str
    SOURCE_PASSWORD: str

    # Direct Head Office connection used only after an outlet transaction commits.
    HO_SERVER: str
    HO_DATABASE: str
    HO_DB_USERNAME: str
    HO_DB_PASSWORD: str

    # Log DB connection (secondary server — ProductSyncLog, ProductPriceChangeLog, and future log tables)
    LOG_SERVER: str
    LOG_DATABASE: str
    LOG_USER: str
    LOG_PASSWORD: str

    # Central DB name used inside linked server queries from outlets
    CENTRAL_DB: str

    # Linked server name as created on each outlet
    CENTRAL_LINKED_SERVER_NAME: str

    # Local/outlet database name
    LOCAL_DB: str

    # Outlet DB credentials
    OUTLET_DB_USER: str
    OUTLET_DB_PASSWORD: str

    # Concurrency
    MAX_CONCURRENT_SYNCS: int = Field(default=20, ge=1, le=100)
    THREAD_POOL_MAX_WORKERS: int = Field(default=40, ge=4, le=200)

    # Connection and query timeouts (seconds)
    CONNECT_TIMEOUT: int = Field(default=10, ge=1)
    QUERY_TIMEOUT: int = Field(default=120, ge=1)
    OUTLET_SYNC_TIMEOUT: int = Field(default=180, ge=1)
    FULL_SYNC_TIMEOUT: int = Field(default=10800, ge=1)

    # Comma-separated outlet codes skipped by full cycles. Individual manual
    # outlet syncs remain available for diagnostics.
    EXCLUDED_OUTLETS: str = ""

    # Scheduling
    SYNC_INTERVAL_MINUTES: int = Field(default=30, ge=1)

    # Retry settings
    RETRY_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=20)
    RETRY_BASE_DELAY: int = Field(default=30, ge=1)  # seconds

    # API server
    API_HOST: str = "0.0.0.0"
    API_PORT: int = Field(default=8000, ge=1, le=65535)
    ADMIN_API_KEY: str = ""
    VIEWER_API_KEY: str = ""
    ADMIN_RATE_LIMIT_PER_MINUTE: int = Field(default=30, ge=1, le=1000)

    # Price change log retention
    PRICE_CHANGE_RETENTION_DAYS: int = Field(default=90, ge=1, le=3650)
    PRICE_CHANGE_INSERT_BATCH_SIZE: int = Field(default=500, ge=1, le=2000)
    ENABLE_PRODUCT_PRICE_CHANGE_LOG: bool = False
    ENABLE_PRODUCT_SYNC_LOG_HISTORY: bool = False

    # Logging
    LOG_DIR: str = "logs"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @model_validator(mode="after")
    def validate_timeout_hierarchy(self):
        if self.THREAD_POOL_MAX_WORKERS < self.MAX_CONCURRENT_SYNCS:
            raise ValueError(
                "THREAD_POOL_MAX_WORKERS must be greater than or equal to "
                "MAX_CONCURRENT_SYNCS"
            )
        if self.OUTLET_SYNC_TIMEOUT <= self.QUERY_TIMEOUT:
            raise ValueError("OUTLET_SYNC_TIMEOUT must be greater than QUERY_TIMEOUT")
        if self.FULL_SYNC_TIMEOUT <= self.OUTLET_SYNC_TIMEOUT:
            raise ValueError("FULL_SYNC_TIMEOUT must be greater than OUTLET_SYNC_TIMEOUT")
        return self


# Singleton settings instance
settings = Settings()
