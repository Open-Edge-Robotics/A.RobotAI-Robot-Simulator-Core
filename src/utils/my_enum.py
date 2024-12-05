from enum import Enum

class SimulationStatus(Enum):
    EMPTY = "Empty" # 시뮬레이션에 속한 인스턴스가 없는 경우
    ACTIVE = "Active" # 시뮬레이션에 속한 모든 인스턴스의 pod status가 Running인 경우
    INACTIVE = "Inactive" # 인스턴스의 pod status가 하나라도 Running이 아닌 경우

class PodStatus(Enum):
    """Pod의 실제 status"""
    RUNNING = "Running"

class InstanceStatus(Enum):
    """Pod status를 Instance status로 변환"""
    READY = "Ready"

class API(Enum):
    # 템플릿
    GET_TEMPLATES = "템플릿 목록 조회"
    CREATE_TEMPLATE = "템플릿 생성"
    DELETE_TEMPLATE = "템플릿 삭제"

    # 인스턴스
    CREATE_INSTANCE = "인스턴스 생성"
    GET_INSTANCES = "인스턴스 목록 조회"
    GET_INSTANCE = "인스턴스 상세 조회"
    CONTROL_INSTANCE = "인스턴스 실행/중지"
    RUN_INSTANCE = "인스턴스 실행"
    STOP_INSTANCE = "인스턴스 실행 중지"
    DELETE_INSTANCE = "인스턴스 삭제"

    #시뮬레이션
    GET_SIMULATIONS = "시뮬레이션 목록 조회"
    CREATE_SIMULATION = "시뮬레이션 생성"
    CONTROL_SIMULATION = "시뮬레이션 실행/중지"
    RUN_SIMULATION = "시뮬레이션 실행"
    STOP_SIMULATION = "시뮬레이션 실행 중지"
    DELETE_SIMULATION = "시뮬레이션 삭제"
