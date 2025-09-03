from sqlalchemy import ForeignKey, Integer, String, DateTime, Enum as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from .enums import StepStatus
from database.db_conn import Base


class SimulationStep(Base):
    __tablename__ = "simulation_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    simulation_id: Mapped[int] = mapped_column(ForeignKey("simulations.id", ondelete="CASCADE"))

    step_order: Mapped[int] = mapped_column(nullable=False)
    template_id: Mapped[int] = mapped_column(ForeignKey("templates.template_id", ondelete="CASCADE"), nullable=False)
    autonomous_agent_count: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_time: Mapped[int] = mapped_column(Integer, nullable=False)
    delay_after_completion: Mapped[int] = mapped_column(Integer, default=0)

    # 반복 실행 정보 (실시간 모니터링용)
    repeat_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    current_repeat: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    status: Mapped[StepStatus] = mapped_column(
        PgEnum(StepStatus, name="step_status_enum", create_constraint=True),
        default=StepStatus.PENDING
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    template: Mapped["Template"] = relationship(
        "Template",
        uselist=False,
        lazy="selectin"
    )
    
    def __repr__(self) -> str:
        return f"SimulationStep => Step {self.step_order} ({self.status})"
    
    @property
    def progress_percentage(self) -> float:
        """현재 진행률을 백분율로 반환"""
        if self.repeat_count <= 0:
            return 0.0
        return (self.current_repeat / self.repeat_count) * 100
    
    @property
    def execution_duration(self) -> float:
        """실행 시간을 초 단위로 반환"""
        if not self.started_at:
            return 0.0
        
        end_time = self.completed_at or self.failed_at
        if not end_time:
            return 0.0
            
        return (end_time - self.started_at).total_seconds()
    
