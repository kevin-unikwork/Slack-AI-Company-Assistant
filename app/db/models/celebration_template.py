from sqlalchemy import String, DateTime, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone

from app.db.models.user import Base


class CelebrationTemplate(Base):
    __tablename__ = "celebration_templates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    template_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "birthday" or "anniversary"
    target_slack_id: Mapped[str | None] = mapped_column(String(50), nullable=True)  # NULL = global template
    message_template: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_slack_id: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        scope = f"user={self.target_slack_id}" if self.target_slack_id else "global"
        return f"<CelebrationTemplate type={self.template_type} {scope}>"
