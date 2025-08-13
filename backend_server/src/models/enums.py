from enum import Enum


class PatternType(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"

class SimulationStatus(str, Enum):
    INITIATING = "INITIATING"  # 생성/환경 구성 중
    READY = "READY"            # 실행 대기
    RUNNING = "RUNNING"        # 실행 중
    PAUSED = "PAUSED"          # 일시 중지
    COMPLETED = "COMPLETED"    # 정상 완료
    FAILED = "FAILED"          # 오류 발생
    CANCELLED = "CANCELLED"    # 사용자 취소

class StepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class GroupStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

class InstanceStatus(str, Enum):
    PENDING = "PENDING"      # 생성 대기
    CREATING = "CREATING"    # Pod 생성 중
    RUNNING = "RUNNING"      # Pod 실행 중
    COMPLETED = "COMPLETED"  # 완료
    FAILED = "FAILED"        # 실패 (리소스 정리 대상)
    RETRYING = "RETRYING"    # 재시도 중
    CANCELLED = "CANCELLED"  # 취소됨