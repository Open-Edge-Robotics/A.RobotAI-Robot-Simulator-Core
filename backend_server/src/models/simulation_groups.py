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
    expected_pods_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    status: Mapped[GroupStatus] = mapped_column(
        PgEnum(GroupStatus, name="group_status_enum", create_constraint=True),
        default=GroupStatus.PENDING
    )

    actual_start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Pod 생성 결과 추적 (리소스 누수 방지 & 부분 실패 처리용)
    successful_agents: Mapped[int] = mapped_column(Integer, default=0)
    failed_agents: Mapped[int] = mapped_column(Integer, default=0)
    created_pods_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    template: Mapped["Template"] = relationship(
        "Template",
        uselist=False,
        lazy="selectin"
    )
    
    def __repr__(self) -> str:
        return f"SimulationGroup => {self.group_name} ({self.status})"
    
