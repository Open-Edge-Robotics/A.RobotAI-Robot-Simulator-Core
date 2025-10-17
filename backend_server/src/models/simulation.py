from datetime import datetime, timezone
from typing import List, Optional


from .simulation_groups import SimulationGroup
from .simulation_steps import SimulationStep
from .instance import Instance

from .enums import PatternType, SimulationStatus
from sqlalchemy import Float, String, DateTime, Integer, ForeignKey, Enum as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.db_conn import Base


class Simulation(Base):
    __tablename__ = 'simulations'

    # 기본 정보
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(String(100), nullable=False)
    pattern_type: Mapped[str] = mapped_column(
        PgEnum(PatternType, name = "pattern_type_enum", create_constraint = True), 
        nullable = False 
    )
    mec_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # 상태 및 namespace
    status: Mapped[SimulationStatus] = mapped_column(
        PgEnum(SimulationStatus, name="simulation_status_enum", create_constraint=True),
        default=SimulationStatus.INITIATING,
        nullable=False
    )
    namespace: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    
    # 진행률 추적 (실시간 모니터링용)
    total_expected_pods: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_pods: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # 백그라운드 작업 관리
    background_task_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    pod_creation_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    pod_creation_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # 실행 통계
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # 메타 정보
    created_by: Mapped[Optional[str]] = mapped_column(String(100), nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    # 관계
    steps: Mapped[Optional[List[SimulationStep]]] = relationship(
        backref="simulation", cascade="all, delete-orphan", lazy="select"
    )
    groups: Mapped[Optional[List[SimulationGroup]]] = relationship(
        backref="simulation", cascade="all, delete-orphan", lazy="select"
    )
    instances: Mapped[List[Instance]] = relationship(back_populates="simulation", lazy="select")

    def __repr__(self) -> str:
        return f"Simulation => {self.name} ({self.pattern_type})"
    
    def mark_as_deleted(self):
        self.deleted_at = datetime.now(timezone.utc)

