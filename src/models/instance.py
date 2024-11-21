from datetime import datetime
from typing import List

from sqlalchemy import String, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship, DeclarativeBase

from src.database.connection import Base
from src.models.simulation import Simulation


class Instance(Base):
    __tablename__ = 'instances'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at : Mapped[DateTime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    instance_set: Mapped[List["InstanceSet"]] = relationship(back_populates="instance")

    template_id: Mapped[int] = mapped_column(ForeignKey("templates.template_id", ondelete="CASCADE"))
    template: Mapped["Template"] = relationship(back_populates="instance")

    def __repr__(self) -> str:
        return f"Instance => {self.name}"

# TODO: 파일 분리?
class InstanceSet(Base):
    __tablename__ = 'instances_sets'

    id: Mapped[int] = mapped_column(primary_key=True)

    instance_count: Mapped[int]

    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id", ondelete="CASCADE"))
    instance: Mapped["Instance"] = relationship(back_populates="instance_set")

    simulation_id: Mapped[int] = mapped_column(ForeignKey("simulations.id", ondelete="CASCADE"))
    simulation: Mapped["Simulation"] = relationship(back_populates="instance_set")
