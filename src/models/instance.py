from datetime import datetime

from sqlalchemy import String, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.db_conn import Base
from src.models.simulation import Simulation


class Instance(Base):
    __tablename__ = 'instances'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(String(100), nullable=False)
    pod_name: Mapped[str] = mapped_column(String(30), nullable=True) # nullable=True: instance_id 받아서 이름 생성해야 함
    pod_namespace: Mapped[str] = mapped_column(String(30), nullable=True)

    created_at: Mapped[DateTime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    template_id: Mapped[int] = mapped_column(ForeignKey("templates.template_id", ondelete="CASCADE"))
    template: Mapped["Template"] = relationship(back_populates="instance")

    simulation_id: Mapped[int] = mapped_column(ForeignKey("simulations.id", ondelete="CASCADE"))
    simulation: Mapped["Simulation"] = relationship(back_populates="instance")

    def __repr__(self) -> str:
        return f"Instance => {self.name}"
