from typing import Dict, List, Optional, Protocol, runtime_checkable

@runtime_checkable
class BaseRepository(Protocol):
    """기본 레포지토리 인터페이스"""
    
    async def count_all(self) -> int:
        """전체 개수 조회"""
        ...

@runtime_checkable  
class SimulationRepositoryInterface(Protocol):
    """시뮬레이션 레포지토리 인터페이스"""
    
    async def find_all_with_pagination(
        self, 
        pagination: "PaginationParams",
        pattern_type: Optional["PatternType"] = None,
        status: Optional["SimulationStatus"] = None
    ) -> List["Simulation"]:
        """페이지네이션된 시뮬레이션 목록 조회"""
        ...
    
    async def count_all(
        self,
        pattern_type: Optional["PatternType"] = None,
        status: Optional["SimulationStatus"] = None
    ) -> int:
        """전체 시뮬레이션 개수 조회 (필터 포함)"""
        ...
    
    async def exists_by_id(self, simulation_id: int) -> bool:
        """시뮬레이션 존재 여부 확인"""
        ...
    
    async def get_overview(self) -> Dict[str, int]:
        """시뮬레이션 전체 개요 조회 - 전체/상태별 개수"""
        ...
    
    async def find_by_id(self, simulation_id: int) -> Optional["Simulation"]:
        """단일 시뮬레이션 조회"""
        ...
    
    async def find_steps_with_template(self, simulation_id: int) -> List["SimulationStep"]:
        """Sequential 패턴용 Step 조회 + Template join"""
        ...
    
    async def find_groups_with_template(self, simulation_id: int) -> List["SimulationGroup"]:
        """Parallel 패턴용 Group 조회 + Template join"""
        ...

@runtime_checkable
class MecRepositoryInterface(Protocol):
    """MEC 레포지토리 인터페이스"""
    
    async def count_all(self) -> int:
        """전체 MEC 개수 조회"""
        ...

@runtime_checkable
class InstanceRepositoryInterface(Protocol):
    """인스턴스 레포지토리 인터페이스"""
    
    async def count_all(self) -> int:
        """전체 인스턴스 개수 조회"""
        ...