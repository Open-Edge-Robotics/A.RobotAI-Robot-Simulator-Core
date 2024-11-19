from datetime import datetime

from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from src.database.connection import Base


class Template(Base):
    __tablename__ = 'templates'

    template_id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str] = mapped_column(String(100))
    bag_file_path: Mapped[str] = mapped_column(nullable=False)
    topics: Mapped[str] = mapped_column()

    created_at: Mapped[DateTime] = mapped_column(DateTime, default=datetime.now)
