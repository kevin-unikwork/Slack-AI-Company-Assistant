from pydantic import field_validator
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    PydanticBaseSettingsSource,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Prefer values from .env over process-level env to avoid accidental collisions.
        return (init_settings, dotenv_settings, env_settings, file_secret_settings)

    app_env: str = "development"
    secret_key: str
    debug: bool = False

    slack_bot_token: str
    slack_signing_secret: str
    slack_app_token: str

    openai_api_key: str

    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    @field_validator("database_url", mode="before")
    @classmethod
    def fix_database_url(cls, v: str) -> str:
        """Fix for Railway/Aiven/Heroku where DATABASE_URL starts with postgres://"""
        if v and v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v and v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        
        # Ensure +asyncpg is present for async engines
        if v and "postgresql" in v and "+asyncpg" not in v:
            v = v.replace("postgresql", "postgresql+asyncpg", 1)
            
        return v

    chroma_persist_dir: str = "./chroma_db"

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    standup_cron_hour: int = 3
    standup_cron_minute: int = 30
    standup_summary_hour: int = 4
    standup_summary_minute: int = 30
    standup_channel: str = "#standup"

    onboarding_welcome_channel: str = "#general"
    hr_private_channel: str = "#hr-feedback"


settings = Settings()
