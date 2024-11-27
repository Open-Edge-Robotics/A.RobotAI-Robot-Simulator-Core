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
    created_at: Mapped[DateTime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    pod: Mapped["Pod"] = relationship(back_populates="instance")

    template_id: Mapped[int] = mapped_column(ForeignKey("templates.template_id", ondelete="CASCADE"))
    template: Mapped["Template"] = relationship(back_populates="instance")

    def __repr__(self) -> str:
        return f"Instance => {self.name}"


# TODO: 파일 분리?
class Pod(Base):
    __tablename__ = 'pods'

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(String(30))
    namespace: Mapped[str] = mapped_column(String(30), nullable=True)

    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id", ondelete="CASCADE"))
    instance: Mapped["Instance"] = relationship(back_populates="pod")

    simulation_id: Mapped[int] = mapped_column(ForeignKey("simulations.id", ondelete="CASCADE"))
    simulation: Mapped["Simulation"] = relationship(back_populates="pod")
