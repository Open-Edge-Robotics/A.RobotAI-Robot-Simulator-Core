import pytest
from sqlite3 import DatabaseError
from .conftest import create_mock_simulations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../')) 
from schemas.pagination import PaginationParams

class TestSimulationServiceFiltering:
    @pytest.mark.asyncio
    async def test_정상_케이스_패턴_필터링(self, service, mock_repository):
        pagination = PaginationParams(page=1, size=10)
        mock_simulations = create_mock_simulations(count=4)
        filtered_sims = [sim for sim in mock_simulations if sim.pattern_type == "sequential"]
        mock_repository.find_all_with_pagination.return_value = filtered_sims
        mock_repository.count_all.return_value = len(filtered_sims)

        result, meta = await service.get_simulations_with_pagination(
            pagination=pagination,
            pattern_type="sequential"
        )

        assert all(sim.pattern_type == "sequential" for sim in result)
        assert meta.total_items == len(filtered_sims)

    @pytest.mark.asyncio
    async def test_정상_케이스_상태_필터링(self, service, mock_repository):
        pagination = PaginationParams(page=1, size=10)
        mock_simulations = create_mock_simulations(count=4)
        filtered_sims = [sim for sim in mock_simulations if sim.status == "READY"]
        mock_repository.find_all_with_pagination.return_value = filtered_sims
        mock_repository.count_all.return_value = len(filtered_sims)

        result, meta = await service.get_simulations_with_pagination(
            pagination=pagination,
            status="READY"
        )

        assert all(sim.status == "READY" for sim in result)
        assert meta.total_items == len(filtered_sims)

    @pytest.mark.asyncio
    async def test_정상_케이스_패턴_상태_동시_필터링(self, service, mock_repository):
        pagination = PaginationParams(page=1, size=10)
        mock_simulations = create_mock_simulations(count=6)
        filtered_sims = [
            sim for sim in mock_simulations
            if sim.pattern_type == "parallel" and sim.status == "RUNNING"
        ]
        mock_repository.find_all_with_pagination.return_value = filtered_sims
        mock_repository.count_all.return_value = len(filtered_sims)

        result, meta = await service.get_simulations_with_pagination(
            pagination=pagination,
            pattern_type="parallel",
            status="RUNNING"
        )

        assert all(sim.pattern_type == "parallel" and sim.status == "RUNNING" for sim in result)
        assert meta.total_items == len(filtered_sims)

    @pytest.mark.asyncio
    async def test_경계_케이스_필터_결과_없음(self, service, mock_repository):
        pagination = PaginationParams(page=1, size=10)
        mock_repository.find_all_with_pagination.return_value = []
        mock_repository.count_all.return_value = 0

        result, meta = await service.get_simulations_with_pagination(
            pagination=pagination,
            pattern_type="sequential",
            status="FAILED"
        )

        assert result == []
        assert meta.total_items == 0

    @pytest.mark.asyncio
    async def test_예외_케이스_Repository_오류_필터링(self, service, mock_repository):
        pagination = PaginationParams(page=1, size=10)
        mock_repository.count_all.side_effect = DatabaseError()

        with pytest.raises(DatabaseError):
            await service.get_simulations_with_pagination(
                pagination=pagination,
                pattern_type="parallel"
            )
