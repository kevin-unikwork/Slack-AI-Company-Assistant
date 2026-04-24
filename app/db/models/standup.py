from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.user import Base


class StandupResponse(Base):
    __tablename__ = "standup_responses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_slack_id: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    yesterday: Mapped[str | None] = mapped_column(Text, nullable=True)
    today: Mapped[str | None] = mapped_column(Text, nullable=True)
    blockers: Mapped[str | None] = mapped_column(Text, nullable=True)
    projects_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    step: Mapped[int] = mapped_column(default=0, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    def __repr__(self) -> str:
        return f"<StandupResponse id={self.id} user={self.user_slack_id} complete={self.is_complete}>"


class StandupSummary(Base):
    __tablename__ = "standup_summaries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    channel_id: Mapped[str] = mapped_column(String(80), nullable=False)
    responded_count: Mapped[int] = mapped_column(default=0, nullable=False)
    total_count: Mapped[int] = mapped_column(default=0, nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<StandupSummary id={self.id} channel={self.channel_id}>"
