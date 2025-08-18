from datetime import datetime
from types import SimpleNamespace
from typing import List
import pytest
from unittest.mock import AsyncMock, Mock 

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../'))
from crud.simulation import SimulationService
from repositories.simulation_repository import SimulationRepository 

@pytest.fixture
def mock_repository():
    """Repository 모킹"""
    repository = Mock(spec=SimulationRepository)
    repository.find_all_with_pagination = AsyncMock()
    repository.count_all = AsyncMock()
    return repository

@pytest.fixture
def service(mock_repository):
    """의존성이 주입된 SimulationService 인스턴스"""
    return SimulationService(
        session=None,
        sessionmaker=None,
        repository=mock_repository
    )
    
def create_mock_simulation(
    sim_id: int = 1,
    name: str = "Test Simulation",
    description: str = "Test Simulation Description",
    pattern_type: str = "sequential",
    status: str = "READY",
    mec_id: str = "mec-001",
    created_at: datetime = None,
    updated_at: datetime = None
) -> Mock:
    """Simulation Mock 객체 팩토리"""
    return SimpleNamespace(
        id=sim_id,
        name=name,
        description=description,
        pattern_type=pattern_type,
        status=status,
        mec_id=mec_id,
        created_at=created_at or datetime.now(),
        updated_at=updated_at or datetime.now()
    )

def create_mock_simulations(count: int = 2) -> List[Mock]:
    """여러 Simulation Mock 객체를 생성하는 팩토리"""
    return [
        create_mock_simulation(
            sim_id=i,
            name=f"Test Simulation {i}",
            description=f"Test Simulation Description {i}",
            pattern_type="sequential" if i % 2 == 1 else "parallel",
            status="READY" if i % 2 == 1 else "RUNNING",
            mec_id=f"mec-{i:03d}"
        )
        for i in range(1, count + 1)
    ]