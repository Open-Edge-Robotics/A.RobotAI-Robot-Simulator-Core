from enum import Enum


class PatternType(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"

class SimulationStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"
    
class PodCreationStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"  # 부분 실패 처리용
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class StepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class GroupStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class InstanceStatus(str, Enum):
    PENDING = "PENDING"      # 생성 대기
    CREATING = "CREATING"    # Pod 생성 중
    RUNNING = "RUNNING"      # Pod 실행 중
    COMPLETED = "COMPLETED"  # 완료
    FAILED = "FAILED"        # 실패 (리소스 정리 대상)
    RETRYING = "RETRYING"    # 재시도 중
    CANCELLED = "CANCELLED"  # 취소됨