import traceback
from typing import Optional, Annotated
from fastapi import APIRouter, Depends, Path, Body, status
from fastapi.responses import JSONResponse
from exception.simulation_exceptions import SimulationError
from exception.template_exceptions import TemplateError
from schemas.simulation_pattern import (
    PatternCreateRequestDTO,
    PatternUpdateRequestDTO,
    PatternDeleteRequestDTO,
    PatternResponseDTO,
)
from crud.simulation_pattern import SimulationPatternService, get_simulation_pattern_service

router = APIRouter(
    prefix="/simulation/{simulation_id}/pattern",
    tags=["Simulation Pattern"],
)

# -----------------------------
# 공통 예외 처리 헬퍼 (HTTP 상태 코드 반영)
# -----------------------------
async def handle_exceptions(func, *args, **kwargs):
    try:
        # 정상 처리
        result: PatternResponseDTO = await func(*args, **kwargs)
        # 성공 시, result.statusCode를 HTTP 상태 코드로 활용
        return JSONResponse(
            status_code=result.status_code,
            content=result.model_dump()
        )

    except SimulationError as e:
        # SimulationError 발생 시 400
        resp = PatternResponseDTO(statusCode=400, data=None, message=str(e))
        return JSONResponse(status_code=400, content=resp.model_dump())

    except TemplateError as e:
        # TemplateError 발생 시 400
        resp = PatternResponseDTO(statusCode=400, data=None, message=str(e))
        return JSONResponse(status_code=400, content=resp.model_dump())

    except Exception as e:
        # 기타 예외 500
        resp = PatternResponseDTO(statusCode=500, data=None, message=str(e))
        return JSONResponse(status_code=500, content=resp.model_dump())

# -----------------------------
# 패턴 생성
# -----------------------------
@router.post(
    "",
    response_model=PatternResponseDTO,
    summary="시뮬레이션 패턴 생성",
    description="""
    시뮬레이션에 새로운 패턴을 생성합니다.
    - Step 또는 Group 중 하나를 필수로 지정해야 합니다.
    - Step: 순차 패턴, Group: 병렬 패턴
    """
)
async def create_pattern(
    simulation_id: Annotated[int, Path(description="시뮬레이션 ID")],
    body: Annotated[PatternCreateRequestDTO, Body(
        description="패턴 생성 정보 (Step 또는 Group 중 하나)",
        examples={
            "step_example": {
                "summary": "Step 패턴 예시",
                "value": {"step": {"stepOrder": 1, "templateId": 101, "autonomousAgentCount": 3}}
            },
            "group_example": {
                "summary": "Group 패턴 예시",
                "value": {"group": {"groupId": 1, "templateId": 102, "autonomousAgentCount": 5}}
            }
        }
    )],
    service: Annotated[SimulationPatternService, Depends(get_simulation_pattern_service)]
) -> PatternResponseDTO:
    return await handle_exceptions(service.create_pattern, simulation_id, body)


# -----------------------------
# 패턴 삭제
# -----------------------------
@router.delete(
    "",
    response_model=PatternResponseDTO,
    summary="시뮬레이션 패턴 삭제",
    description="""
    시뮬레이션의 기존 패턴을 삭제합니다.
    - Step 또는 Group 중 하나를 지정해야 합니다.
    - Step: 순차 패턴, Group: 병렬 패턴
    """
)
async def delete_pattern(
    simulation_id: Annotated[int, Path(description="시뮬레이션 ID")],
    body: Annotated[PatternDeleteRequestDTO, Body(
        description="삭제할 패턴 정보 (Step 또는 Group 중 하나)",
        examples={
            "step_example": {"summary": "Step 삭제", "value": {"step": {"stepOrder": 1}}},
            "group_example": {"summary": "Group 삭제", "value": {"group": {"groupId": 1}}}
        }
    )],
    service: Annotated[SimulationPatternService, Depends(get_simulation_pattern_service)]
) -> PatternResponseDTO:
    return await handle_exceptions(service.delete_pattern, simulation_id, body)


# -----------------------------
# 패턴 수정
# -----------------------------
@router.put(
    "",
    response_model=PatternResponseDTO,
    summary="시뮬레이션 패턴 수정",
    description="""
    시뮬레이션의 기존 패턴을 수정합니다.
    - Step 또는 Group 중 하나만 지정 가능
    - Step: 순차 패턴, Group: 병렬 패턴
    """
)
async def update_pattern(
    simulation_id: Annotated[int, Path(description="시뮬레이션 ID")],
    body: Annotated[PatternUpdateRequestDTO, Body(
        description="수정할 패턴 정보 (Step 또는 Group 중 하나)",
        examples={
            "step_example": {
                "summary": "Step 수정",
                "value": {"step": {"stepOrder": 1, "templateId": 101, "autonomousAgentCount": 4}}
            },
            "group_example": {
                "summary": "Group 수정",
                "value": {"group": {"groupId": 1, "templateId": 102, "autonomousAgentCount": 6}}
            }
        }
    )],
    service: Annotated[SimulationPatternService, Depends(get_simulation_pattern_service)]
) -> PatternResponseDTO:
    return await handle_exceptions(service.update_pattern, simulation_id, body)
