from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    secret_key: str = "dev-secret-key"
    debug: bool = False

    slack_bot_token: str = "xoxb-placeholder"
    slack_signing_secret: str = "placeholder"
    slack_app_token: str = "xapp-placeholder"

    openai_api_key: str = "sk-placeholder"

    database_url: str = "postgresql+asyncpg://admin:password@localhost:5432/slackbot"

    @field_validator("database_url", mode="before")
    @classmethod
    def fix_database_url(cls, v: str) -> str:
        """Heroku/Aiven/Railway provide postgres:// — SQLAlchemy async needs postgresql+asyncpg://"""
        if isinstance(v, str) and v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        return v
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = "dev-jwt-secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    vault_master_key: str = ""

    standup_cron_hour: int = 15
    standup_cron_minute: int = 0
    standup_summary_hour: int = 15
    standup_summary_minute: int = 15
    standup_channel: str = "#standup"
    hr_private_channel: str = "#hr-feedback"
    onboarding_welcome_channel: str = "#general"
    kudos_channel: str = "#general"


settings = Settings()