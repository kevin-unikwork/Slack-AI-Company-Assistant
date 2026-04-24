from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.user import Base


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_slack_id: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=__import__("sqlalchemy").sql.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Reminder id={self.id} user={self.user_slack_id} at={self.remind_at} sent={self.is_sent}>"
