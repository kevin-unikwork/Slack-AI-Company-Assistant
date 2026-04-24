from __future__ import annotations

from datetime import datetime, timezone, date

from sqlalchemy import Boolean, Date, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slack_id: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    slack_username: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    manager_slack_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_hr_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_project_manager: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    joined_at: Mapped[date] = mapped_column(Date, default=lambda: date.today(), nullable=False)
    birthday: Mapped[date | None] = mapped_column(Date, nullable=True)

    def __repr__(self) -> str:
        return f"<User id={self.id} slack_id={self.slack_id}>"
