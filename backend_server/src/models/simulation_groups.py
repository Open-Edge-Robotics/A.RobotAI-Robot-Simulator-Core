from sqlalchemy import ForeignKey, Integer, String, DateTime, Enum as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from .enums import GroupStatus
from database.db_conn import Base

class SimulationGroup(Base):
    __tablename__ = "simulation_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    simulation_id: Mapped[int] = mapped_column(ForeignKey("simulations.id", ondelete="CASCADE"))

    group_name: Mapped[str] = mapped_column(String(255))
    template_id: Mapped[int] = mapped_column(ForeignKey("templates.template_id", ondelete="CASCADE"), nullable=False)
    autonomous_agent_count: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_time: Mapped[int] = mapped_column(Integer, nullable=False)
    assigned_area: Mapped[str] = mapped_column(String(255))
    
    # 반복 실행 정보 (실시간 모니터링용)
    repeat_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    current_repeat: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    status: Mapped[GroupStatus] = mapped_column(
        PgEnum(GroupStatus, name="group_status_enum", create_constraint=True),
        default=GroupStatus.PENDING
    )
    
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)
    
    template: Mapped["Template"] = relationship(
        "Template",
        uselist=False,
        lazy="selectin"
    )
    
    def __repr__(self) -> str:
        return f"SimulationGroup => {self.group_name} ({self.status})"
    
    @property
    def calculate_progress(self) -> float:
        """그룹의 상태와 반복 실행 정보를 기반으로 진행률 계산"""
        if self.status == GroupStatus.COMPLETED:
            return 1.0  # 100% 완료
        elif self.status == GroupStatus.FAILED or self.status == GroupStatus.STOPPED:
            return 0.0  # 실패/중단된 경우 0%
        elif self.status == GroupStatus.RUNNING:
            # 실행 중인 경우 반복 실행 정보를 고려한 진행률 계산
            if self.repeat_count > 0:
                return min(self.current_repeat / self.repeat_count, 1.0)
            else:
                return 0.0  # 반복 정보가 없으면 0%로 설정
        else:  # PENDING 상태
            return 0.0
