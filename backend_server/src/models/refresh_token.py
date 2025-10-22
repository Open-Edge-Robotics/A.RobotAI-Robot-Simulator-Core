from datetime import datetime, timedelta, timezone
from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database.db_conn import Base

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    token: Mapped[str] = mapped_column(nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=datetime.now)

    user: Mapped["User"] = relationship("User", back_populates="refresh_tokens")

    # -----------------------------
    # RefreshToken 도메인 책임 메서드
    # -----------------------------

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at

    def extend_expiry(self, days: int = 7):
        self.expires_at = datetime.now(timezone.utc) + timedelta(days=days)
