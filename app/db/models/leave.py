from sqlalchemy import String, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone

from app.db.models.user import Base


class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_slack_id: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    manager_slack_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pending | approved | rejected
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    # Slack message timestamp for manager DM (used for button update)
    manager_message_ts: Mapped[str | None] = mapped_column(String(50), nullable=True)
    manager_channel: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<LeaveRequest id={self.id} user={self.user_slack_id} status={self.status}>"