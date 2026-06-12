from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_url: str = "http://localhost:8000"
    secret_key: str = "change-me-before-production"
    database_url: str = "sqlite:///./panmajster.db"
    database_schema: str = "panmajster"
    storage_provider: str = "local_disk"
    media_root: Path = Path("./data")
    session_days: int = 30
    otp_minutes: int = 10
    max_upload_mb: int = 40

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "Pan Majster <noreply@panmajster.pl>"
    smtp_starttls: bool = True
    admin_emails: str = ""

    openai_api_key: str | None = None
    openai_transcription_model: str = "gpt-4o-mini-transcribe"
    openai_report_model: str = "gpt-4.1-mini"

    worker_enabled: bool = True
    worker_poll_seconds: int = 5

    @property
    def normalized_database_url(self) -> str:
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql://", 1)
        return self.database_url

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def admin_email_set(self) -> set[str]:
        return {
            item.strip().lower()
            for item in self.admin_emails.split(",")
            if item.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
