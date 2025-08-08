from enum import Enum


class PatternType(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"

class SimulationStatus(str, Enum):
    CREATING = "CREATING"
    CREATED = "CREATED"           # 시뮬레이션 메타데이터 생성 완료, Pod 생성 중
    READY = "READY"               # 모든 Pod 생성 완료, 실행 대기 상태
    RUNNING = "RUNNING"           # 실제 시뮬레이션 실행 중
    COMPLETED = "COMPLETED"       # 시뮬레이션 정상 완료
    FAILED = "FAILED"             # 생성 또는 실행 과정에서 오류 발생
    CANCELLED = "CANCELLED"       # 사용자에 의해 취소됨
    PAUSED = "PAUSED"             # 실행 중이던 시뮬레이션 일시정지
    
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