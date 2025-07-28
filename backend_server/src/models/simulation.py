from datetime import datetime
from typing import List, Optional

from sqlalchemy import String, DateTime, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.db_conn import Base


class Simulation(Base):
    __tablename__ = 'simulations'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(String(100), nullable=False)
    namespace: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    template_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("templates.template_id", ondelete="SET NULL")
    )
    template: Mapped[Optional["Template"]] = relationship(back_populates="simulations")

    autonomous_agent_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    execution_time: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    delay_time: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    repeat_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    scheduled_start_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    scheduled_end_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    mec_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    instances: Mapped[List["Instance"]] = relationship(back_populates="simulation", lazy="selectin")

    def __repr__(self) -> str:
        return f"Simulation => {self.name}"

