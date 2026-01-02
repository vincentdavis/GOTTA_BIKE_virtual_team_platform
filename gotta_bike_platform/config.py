"""Application configuration using pydantic-settings."""

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Django Core
    secret_key: str = Field(
        default="django-insecure-change-me-in-production",
        description="Django secret key for cryptographic signing",
    )
    debug: bool = Field(
        default=False,
        description="Enable debug mode (never use True in production)",
    )
    allowed_hosts_str: str = Field(
        default="localhost,127.0.0.1",
        alias="ALLOWED_HOSTS",
        description="Comma-separated list of allowed host/domain names",
    )
    internal_ips_str: str = Field(
        default="127.0.0.1",
        alias="INTERNAL_IPS",
        description="Comma-separated internal IPs for debug toolbar",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def allowed_hosts(self) -> list[str]:
        """Get allowed hosts as a list.

        Returns:
            List of allowed host strings.

        """
        return [h.strip() for h in self.allowed_hosts_str.split(",") if h.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def internal_ips(self) -> list[str]:
        """Get internal IPs as a list.

        Returns:
            List of internal IP strings.

        """
        return [ip.strip() for ip in self.internal_ips_str.split(",") if ip.strip()]

    # Database
    database_url: str = Field(
        default="sqlite:///db.sqlite3",
        description="Database connection URL",
    )

    # S3 Storage (Railway.app or any S3-compatible service)
    aws_s3_endpoint_url: str | None = Field(
        default=None,
        description="S3 endpoint URL (e.g., https://storage.railway.app)",
    )
    aws_s3_region_name: str = Field(
        default="auto",
        description="S3 region name",
    )
    aws_access_key_id: str | None = Field(
        default=None,
        description="S3 access key ID",
    )
    aws_secret_access_key: str | None = Field(
        default=None,
        description="S3 secret access key",
    )
    aws_storage_bucket_name: str | None = Field(
        default=None,
        description="S3 bucket name for static/media files",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def use_s3_storage(self) -> bool:
        """Check if S3 storage is configured.

        Returns:
            True if all required S3 settings are provided.

        """
        return all([
            self.aws_s3_endpoint_url,
            self.aws_access_key_id,
            self.aws_secret_access_key,
            self.aws_storage_bucket_name,
        ])

    # Logfire (optional)
    logfire_token: str | None = Field(
        default=None,
        description="Logfire API token for observability",
    )
    logfire_environment: str = Field(
        default="development",
        description="Logfire environment name (e.g., development, staging, production)",
    )

    # Discord OAuth (for user authentication)
    discord_client_id: str | None = Field(
        default=None,
        description="Discord OAuth application client ID",
    )
    discord_client_secret: str | None = Field(
        default=None,
        description="Discord OAuth application client secret",
    )

    # Google Service Account
    google_credentials_base64: str | None = Field(
        default=None,
        description="Base64-encoded Google service account JSON credentials",
    )

    # CORS
    cors_allowed_origins_str: str = Field(
        default="",
        alias="CORS_ALLOWED_ORIGINS",
        description="Comma-separated list of allowed CORS origins (use * for all)",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_allowed_origins(self) -> list[str]:
        """Get CORS allowed origins as a list.

        Returns:
            List of allowed origin strings.

        """
        if not self.cors_allowed_origins_str:
            return []
        return [o.strip() for o in self.cors_allowed_origins_str.split(",") if o.strip()]

    # Superuser (optional - for auto-creation)
    superuser_username: str | None = Field(
        default=None,
        description="Default superuser username for auto-creation",
    )
    superuser_email: str | None = Field(
        default=None,
        description="Default superuser email for auto-creation",
    )
    superuser_password: str | None = Field(
        default=None,
        description="Default superuser password for auto-creation",
    )


settings = Settings()  # ty:ignore[missing-argument]
