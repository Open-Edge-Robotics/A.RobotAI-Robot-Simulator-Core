from enum import Enum

class SimulationStatus(Enum):
    NOTHING = "--" # 시뮬레이션에 속한 인스턴스가 없는 경우
    RUNNING = "Running" # 시뮬레이션에 속한 모든 인스턴스의 pod status가 Running인 경우

class PodStatus(Enum):
    RUNNING = "Running"