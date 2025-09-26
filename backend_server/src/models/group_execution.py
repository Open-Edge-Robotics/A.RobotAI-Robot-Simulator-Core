from datetime import datetime, timezone
from typing import Optional
from enum import Enum

from sqlalchemy import String, DateTime, Integer, ForeignKey, Enum as PgEnum, BIGINT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.db_conn import Base


class GroupExecution(Base):
    __tablename__ = "group_executions"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    execution_id: Mapped[int] = mapped_column(
        ForeignKey("simulation_executions.id", ondelete="CASCADE"),
        nullable=False
    )
    group_id: Mapped[int] = mapped_column(Integer, nullable=False)
    
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    
    # 실행 시간 추적
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    autonomous_agent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_repeat: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_repeats: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    
    # 메타 정보
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    execution: Mapped["SimulationExecution"] = relationship("SimulationExecution", lazy="select")