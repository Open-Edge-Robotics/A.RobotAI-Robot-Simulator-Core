from datetime import datetime
from typing import List

from sqlalchemy import String, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship, DeclarativeBase

from src.database.connection import Base


class Instance(Base):
    __tablename__ = 'instances'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(String(100), nullable=False) # TODO: 길이 정하기
    created_at : Mapped[DateTime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    template_set: Mapped[List["TemplateSet"]] = relationship(back_populates="instance")

    def __repr__(self) -> str:
        return f"Instance => {self.name}"

# TODO: 파일 분리?
class TemplateSet(Base):
    __tablename__ = 'template_sets'

    id: Mapped[int] = mapped_column(primary_key=True)

    # TODO: template_id: Mapped[int] = mapped_column(ForeignKey("template.id", ondelete="CASCADE"))
    template_count: Mapped[int]

    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id", ondelete="CASCADE"))
    instance: Mapped["Instance"] = relationship(back_populates="template_set")
