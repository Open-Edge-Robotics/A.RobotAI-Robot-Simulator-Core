from datetime import datetime
from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from models.enums import UserRole
from database.db_conn import Base
from utils.security import hash_password, verify_password

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now)
    role: Mapped[UserRole] = mapped_column(default=UserRole.GENERAL, nullable=False)

    refresh_tokens: Mapped[list] = relationship("RefreshToken", back_populates="user")

    # -----------------------------
    # User 도메인 책임 메서드
    # -----------------------------

    def set_password(self, raw_password: str):
        self.password_hash = hash_password(raw_password)

    def verify_password(self, raw_password: str) -> bool:
        result = verify_password(raw_password, self.password_hash)
        return result
    
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN
    
    
