from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, ForeignKey, DateTime, Enum as PgEnum, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .enums import InstanceStatus
from database.db_conn import Base


class Instance(Base):
    __tablename__ = 'instances'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    pod_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    pod_namespace: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    status: Mapped[InstanceStatus] = mapped_column(
        PgEnum(InstanceStatus, name="instance_status_enum", create_constraint=True),
        default=InstanceStatus.PENDING,
        nullable=False
    )
    
    # 오류 정보 (리소스 누수 방지 & 부분 실패 처리용)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    # Pod 생성 시간 추적 (실시간 모니터링용)
    pod_creation_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    pod_creation_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Step/Group 정보
    step_order: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 순차 실행용
    group_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)    # 병렬 실행용

    created_at: Mapped[DateTime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    template_id: Mapped[int] = mapped_column(ForeignKey("templates.template_id", ondelete="CASCADE"))
    template: Mapped["Template"] = relationship(back_populates="instances")

    simulation_id: Mapped[int] = mapped_column(ForeignKey("simulations.id", ondelete="CASCADE"))
    simulation: Mapped["Simulation"] = relationship(back_populates="instances")

    def __repr__(self) -> str:
        return f"Instance => {self.name}"

