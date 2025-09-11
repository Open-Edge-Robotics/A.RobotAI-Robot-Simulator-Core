from enum import Enum

class ViewType(str, Enum):
    """API 응답 뷰 타입"""
    DETAIL = "detail"
    DASHBOARD = "dashboard"
class ExecutionStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    STOPPED = "stopped"

class PatternType(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"

class SimulationStatus(str, Enum):
    INITIATING = "INITIATING"  # 생성/환경 구성 중
    PENDING = "PENDING"            # 실행 대기
    RUNNING = "RUNNING"        # 실행 중
    STOPPED = "STOPPED"        # 정지
    COMPLETED = "COMPLETED"    # 정상 완료
    FAILED = "FAILED"          # 오류 발생
    DELETING = "DELETING"      # 삭제 진행 중
    DELETED = "DELETED"        # 삭제 완료(soft delete)

class StepStatus(str, Enum):
    """
    SimulationStep의 실행 상태를 나타내는 enum
    """
    PENDING = "PENDING"              # 대기 중 (아직 시작되지 않음)
    RUNNING = "RUNNING"              # 실행 중
    COMPLETED = "COMPLETED"          # 성공적으로 완료
    FAILED = "FAILED"                # 실패로 중단
    STOPPED = "STOPPED"              # 사용자에 의해 중지됨


class GroupStatus(str, Enum):
    """
    SimulationGroup의 실행 상태를 나타내는 enum
    """
    PENDING = "PENDING"              # 대기 중 (아직 시작되지 않음)
    RUNNING = "RUNNING"              # 실행 중
    COMPLETED = "COMPLETED"          # 성공적으로 완료
    FAILED = "FAILED"                # 실패로 중단
    STOPPED = "STOPPED"              # 사용자에 의해 중지됨

class InstanceStatus(str, Enum):
    PENDING = "PENDING"      # 생성 대기
    CREATING = "CREATING"    # Pod 생성 중
    RUNNING = "RUNNING"      # Pod 실행 중
    COMPLETED = "COMPLETED"  # 완료
    FAILED = "FAILED"        # 실패 (리소스 정리 대상)
    RETRYING = "RETRYING"    # 재시도 중
    CANCELLED = "CANCELLED"  # 취소됨