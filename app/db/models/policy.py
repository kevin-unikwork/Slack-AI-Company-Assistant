from sqlalchemy import String, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone

from app.db.models.user import Base


class PolicyDocument(Base):
    __tablename__ = "policy_documents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)  # pdf | txt
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chroma_collection: Mapped[str] = mapped_column(String(100), default="company_policies", nullable=False)
    uploaded_by_slack_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        __import__("sqlalchemy").Boolean, default=True, nullable=False
    )

    def __repr__(self) -> str:
        return f"<PolicyDocument id={self.id} filename={self.filename}>"