from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, DateTime, Integer, ForeignKey, Enum as PgEnum, JSON, BIGINT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.enums import PatternType, SimulationExecutionStatus
from database.db_conn import Base

class SimulationExecution(Base):
    __tablename__ = 'simulation_executions'

    # 기본 정보
    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    simulation_id: Mapped[int] = mapped_column(
        Integer, 
        ForeignKey('simulations.id', ondelete='CASCADE'),
        nullable=False
    )
    pattern_type: Mapped[str] = mapped_column(
        PgEnum(PatternType, name = "pattern_type_enum", create_constraint = True), 
        nullable = False 
    )
    
    # 실행 상태
    status: Mapped[SimulationExecutionStatus] = mapped_column(
        PgEnum(SimulationExecutionStatus, name="simulation_execution_status_enum", create_constraint=True),
        default=SimulationExecutionStatus.PENDING,
        nullable=False
    )
    
    # 실행 시간 추적
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), 
        nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), 
        nullable=True
    )
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    
    # 실행 결과
    result_summary: Mapped[Optional[dict]] = mapped_column(
        JSON, 
        nullable=True,
        comment="Step/Group/Instance별 최종 상태 및 성과 지표"
    )
    message: Mapped[Optional[str]] = mapped_column(
        String(500), 
        nullable=True,
        comment="실행 종료 요약 메시지"
    )
    
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
    
    # 관계
    simulation: Mapped["Simulation"] = relationship(
        "Simulation",
        lazy="select"
    )
    
    def __repr__(self) -> str:
        return f"SimulationExecution(id={self.id}, simulation_id={self.simulation_id}, pattern_type={self.pattern_type}, status={self.status.value})"
    
    def start_execution(self):
        """실행 시작 처리"""
        self.status = SimulationExecutionStatus.RUNNING
        self.started_at = datetime.now(timezone.utc)
    
    def complete_execution(self, result_summary: Optional[dict] = None, message: Optional[str] = None):
        """실행 완료 처리"""
        self.status = SimulationExecutionStatus.COMPLETED
        self.finished_at = datetime.now(timezone.utc)
        if result_summary:
            self.result_summary = result_summary
        if message:
            self.message = message
    
    def fail_execution(self, message: Optional[str] = None):
        """실행 실패 처리"""
        self.status = SimulationExecutionStatus.FAILED
        self.finished_at = datetime.now(timezone.utc)
        if message:
            self.message = message
    
    def stop_execution(self, message: Optional[str] = None):
        """실행 중단 처리"""
        self.status = SimulationExecutionStatus.STOPPED
        self.finished_at = datetime.now(timezone.utc)
        if message:
            self.message = message
    
    @property
    def duration(self) -> Optional[float]:
        """실행 시간 계산 (초 단위)"""
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None
    
    @property
    def is_running(self) -> bool:
        """실행 중인지 확인"""
        return self.status == SimulationExecutionStatus.RUNNING
    
    @property
    def is_completed(self) -> bool:
        """완료된 상태인지 확인 (성공/실패/중단)"""
        return self.status in [SimulationExecutionStatus.COMPLETED, SimulationExecutionStatus.FAILED, SimulationExecutionStatus.STOPPED]