from typing import Dict, Any

from models.enums import SimulationStatus
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

    # 상태 매핑
    status_map = {
        SimulationStatus.INITIATING: "생성 중",
        SimulationStatus.PENDING: "대기 중",
        SimulationStatus.RUNNING: "실행 중",
        SimulationStatus.COMPLETED: "완료",
        SimulationStatus.FAILED: "오류",
        SimulationStatus.STOPPED: "중지됨",
    }
    simulation_status = status_map.get(simulation.current_status.status, "알 수 없음")

    return {
        "simulation_id": simulation.simulation_id,
        "simulation_name": simulation.simulation_name,
        "status": simulation.current_status.status,  # Enum 값 그대로
        "simulation_status": simulation_status,      # UI용 한글
        "pattern_type": simulation.pattern_type,
        "total_execution_time": total_execution_time,
        "autonomous_agent_count": total_agent_count
    }
