class SimulationError(Exception):
    """시뮬레이션 패턴 관련 도메인 예외"""
    pass

class SimulationNotFoundError(SimulationError):
    def __init__(self, simulation_id: int):
        super().__init__(f"시뮬레이션을 찾을 수 없습니다. ID: {simulation_id}")

class SimulationStepNotFoundError(SimulationError):
    def __init__(self, step_order: int):
        super().__init__(f"스텝을 찾을 수 없습니다. 순서: {step_order}")

class SimulationGroupNotFoundError(SimulationError):
    def __init__(self, group_id: int):
        super().__init__(f"그룹을 찾을 수 없습니다. ID: {group_id}")

class PatternTypeMismatchError(SimulationError):
    def __init__(self, expected: str, actual: str):
        super().__init__(f"패턴 타입 불일치. 기대값: {expected}, 실제값: {actual}")

class SimulationStatusError(SimulationError):
    def __init__(self, status: str, action: str = None):
        """
        재사용 가능한 시뮬레이션 상태 예외

        Args:
            status: 현재 시뮬레이션 상태
            action: 예외 발생 맥락(예: "패턴 추가", "패턴 수정", "패턴 삭제")
        """
        if action:
            message = f"❌ 시뮬레이션 상태 '{status}'에서는 {action}을(를) 수행할 수 없습니다."
        else:
            message = f"❌ 시뮬레이션 상태 '{status}'에서는 해당 작업을 수행할 수 없습니다."
        super().__init__(message)
