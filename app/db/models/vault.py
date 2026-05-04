from sqlalchemy import String, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone
from app.db.models.user import Base

class UserVault(Base):
    __tablename__ = "user_vaults"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_slack_id: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    key_name: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=lambda: datetime.now(timezone.utc), 
        onupdate=lambda: datetime.now(timezone.utc), 
        nullable=False
    )

    def __repr__(self) -> str:
        return f"<UserVault user={self.user_slack_id} key={self.key_name}>"
