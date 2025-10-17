from datetime import datetime, timezone
from typing import Dict, Any, Optional
import uuid

from models.enums import SimulationExecutionStatus
from schemas.simulation_detail import SimulationData

def extract_simulation_dashboard_data(simulation: SimulationData) -> Dict[str, Any]:
    """시뮬레이션 데이터에서 대시보드 필요 정보 추출 (순차/병렬 + 상태 한글 매핑)"""

    # 총 실행 시간과 총 에이전트 수 계산
    total_execution_time = 0
    total_agent_count = 0

    if simulation.pattern_type == "sequential" and hasattr(simulation.execution_plan, "steps"):
        for step in simulation.execution_plan.steps:
            total_execution_time += step.execution_time * step.repeat_count
            total_agent_count += step.autonomous_agent_count
    elif simulation.pattern_type == "parallel" and hasattr(simulation.execution_plan, "groups"):
        for group in simulation.execution_plan.groups:
            total_execution_time += group.execution_time * group.repeat_count
            total_agent_count += group.autonomous_agent_count

    # latest_execution_status 기반
    if simulation.latest_execution_status:
        latest_execution_status = simulation.latest_execution_status.status
    else:
        latest_execution_status = "실행 기록 없음"

    return {
        "simulation_id": simulation.simulation_id,
        "simulation_name": simulation.simulation_name,
        "latest_execution_status": latest_execution_status,  # UI용 한글
        "pattern_type": simulation.pattern_type,
        "total_execution_time": total_execution_time,
        "autonomous_agent_count": total_agent_count
    }


def generate_temp_group_name(simulation_id: int) -> str:
    """flush 전 UUID8 기반 그룹 이름 생성"""
    return f"sim-{simulation_id}-group-{uuid.uuid4().hex[:8]}"

def generate_final_group_name(simulation_id: int, group_id: int) -> str:
    """flush 후 group_id 기반 최종 그룹 이름 생성"""
    return f"sim-{simulation_id}-group-{group_id}"

# -----------------------------
# 유니크 인스턴스 이름 생성
# -----------------------------
def generate_instance_name(
    simulation_id: int,
    step_order: Optional[int] = None,
    group_id: Optional[int] = None
) -> str:
    """
    UUID8 기반 임시 유니크 이름 생성
    """
    unique_suffix = uuid.uuid4().hex[:8]

    if step_order is not None:
        return f"sim-{simulation_id}-step-{step_order}-instance-{unique_suffix}"
    elif group_id is not None:
        return f"sim-{simulation_id}-group-{group_id}-instance-{unique_suffix}"
    else:
        return f"sim-{simulation_id}-instance-{unique_suffix}"