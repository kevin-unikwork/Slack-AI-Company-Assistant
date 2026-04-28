from sqlalchemy import String, Boolean, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slack_id: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    slack_username: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_hr_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    birthday: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Hashed password for HR admin login (only HR admins need this)
    hashed_password: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Manager's Slack ID for leave notification
    manager_slack_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    def __repr__(self) -> str:
        return f"<User slack_id={self.slack_id} username={self.slack_username}>"