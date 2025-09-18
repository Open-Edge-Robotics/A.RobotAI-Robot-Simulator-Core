from datetime import datetime
from typing import List

from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .instance import Instance
from database.db_conn import Base


class Template(Base):
    __tablename__ = 'templates'

    template_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False)
    type: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str] = mapped_column(String(100), nullable=False)
    bag_file_path: Mapped[str] = mapped_column(nullable=False)
    topics: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)

    instances: Mapped[List[Instance]] = relationship(back_populates="template", lazy="selectin")

    def __repr__(self) -> str:
        return f"Template => {self.type} ({self.template_id})"