from datetime import datetime
from sqlite3 import DatabaseError
from types import SimpleNamespace
from typing import List
import pytest
from unittest.mock import AsyncMock, Mock 
from .conftest import create_mock_simulations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../')) 
from schemas.pagination import PaginationParams

class TestSimlutaionService:
    @pytest.mark.asyncio
    async def test_정상_케이스_데이터_조회_성공(self, service, mock_repository):
        """정상적인 페이지네이션 파라미터로 데이터 조회 성공"""
        # Given
        pagination = PaginationParams(page=1, size=10)
        mock_simulations = create_mock_simulations(count = 2)
        mock_repository.find_all_with_pagination.return_value = mock_simulations
        mock_repository.count_all.return_value = 2
        
        # When
        result, meta = await service.get_simulations_with_pagination(pagination)
        
        # Then
        assert len(result) == 2
        assert meta.total_items == 2
        assert meta.current_page == 1
        
    @pytest.mark.asyncio
    async def test_경계_케이스_빈_결과(self, service, mock_repository):
        """데이터가 없는 경우"""
        # Given
        pagination = PaginationParams(page=1, size=10)
        mock_repository.find_all_with_pagination.return_value = []
        mock_repository.count_all.return_value = 0
        
        # When
        result, meta = await service.get_simulations_with_pagination(pagination)
        
        # Then
        assert len(result) == 0
        assert meta.total_items == 0
        
    @pytest.mark.asyncio
    async def test_pagination_size_none(self, service, mock_repository):
        """pagination.size가 None일 때 기본값으로 안전 처리"""
        pagination = PaginationParams(page=1, size=None)
        mock_repository.find_all_with_pagination.return_value = []
        mock_repository.count_all.return_value = 0

        result, meta = await service.get_simulations_with_pagination(pagination)

        assert result == []
        assert meta.total_items == 0
        assert meta.current_page == 1
        assert meta.page_size == 0  # 실제 조회된 데이터 수
        
    @pytest.mark.asyncio
    async def test_예외_케이스_잘못된_페이지_번호(self, service):
        """잘못된 페이지 번호로 인한 검증 실패"""
        # Given
        pagination = PaginationParams(page=2, size=10)
        total_count = 5
        
        
        # When & Then
        with pytest.raises(ValueError) as exc_info:
            service._validate_pagination_range(pagination, total_count)
        assert "페이지 번호가 범위를 벗어났습니다" in str(exc_info.value)
            
    @pytest.mark.asyncio
    async def test_예외_케이스_Repository_오류(self, service, mock_repository):
        """Repository에서 예외 발생"""
        # Given
        pagination = PaginationParams(page=1, size=10)
        mock_repository.count_all.side_effect = DatabaseError()
        
        # When & Then
        with pytest.raises(DatabaseError):
            await service.get_simulations_with_pagination(pagination)