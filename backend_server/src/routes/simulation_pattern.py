from typing import Optional, Annotated
from fastapi import APIRouter, Depends, HTTPException, Path, Body, status
from schemas.simulation_pattern import (
    PatternCreateRequestDTO,
    PatternCreateResponseDTO,
    PatternDeleteRequestDTO,
    PatternDeleteResponseDTO
)
from crud.simulation_pattern import SimulationPatternService, get_simulation_pattern_service

router = APIRouter(
    prefix="/simulations/{simulation_id}/pattern",
    tags=["Simulation Pattern"]
)

# -----------------------------
# 패턴 생성
# -----------------------------
@router.post(
    "",
    response_model=PatternCreateResponseDTO,
    status_code=status.HTTP_201_CREATED
)
async def create_pattern(
    simulation_id: Annotated[int, Path(description="시뮬레이션 ID")],
    body: Annotated[PatternCreateRequestDTO, Body(description="패턴 생성 정보")],
    service: Annotated[SimulationPatternService, Depends(get_simulation_pattern_service)]
) -> PatternCreateResponseDTO:
    try:
        result = await service.create_pattern(simulation_id, body)
        return result
        
    except ValueError as e:
        # 🎯 비즈니스 로직 검증 실패 -> 400 Bad Request
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )
        
    except Exception as e:
        # 🎯 예상치 못한 에러 -> 500 Internal Server Error  
        print(f"❌ 예상치 못한 에러: {e}")
        raise HTTPException(
            status_code=500,
            detail="내부 서버 오류가 발생했습니다"
        )

# -----------------------------
# 패턴 삭제
# -----------------------------
@router.delete(
    "",
    response_model=PatternDeleteResponseDTO,
    status_code=status.HTTP_200_OK
)
async def delete_pattern(
    simulation_id: Annotated[int, Path(description="시뮬레이션 ID")],
    body: Annotated[PatternDeleteRequestDTO, Body(description="패턴 삭제 정보")],
    service: Annotated[SimulationPatternService, Depends(get_simulation_pattern_service)]
) -> PatternDeleteResponseDTO:
    try:
        result = await service.delete_pattern(simulation_id, body)
        return result
        
    except ValueError as e:
        # 🎯 비즈니스 로직 검증 실패 -> 400 Bad Request
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )
        
    except Exception as e:
        # 🎯 예상치 못한 에러 -> 500 Internal Server Error  
        print(f"❌ 예상치 못한 에러: {e}")
        raise HTTPException(
            status_code=500,
            detail="내부 서버 오류가 발생했습니다"
        )