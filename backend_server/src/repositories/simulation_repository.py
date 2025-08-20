from datetime import datetime, timezone
from typing import Dict, List, Optional
from sqlalchemy import case, select, func, update
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc

from models.simulation_groups import SimulationGroup
from models.simulation_steps import SimulationStep
from models.simulation import Simulation
from models.enums import PatternType, SimulationStatus
from schemas.pagination import PaginationParams


class SimulationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_all_with_pagination(
        self, 
        pagination: PaginationParams,
        pattern_type: Optional[PatternType] = None,
        status: Optional[SimulationStatus] = None
    ) -> List[Simulation]:
        """페이지네이션된 시뮬레이션 목록 조회"""
        query = select(Simulation)
        
        # 필터 적용
        if pattern_type:
            query = query.where(Simulation.pattern_type == pattern_type)
        if status:
            query = query.where(Simulation.status == status)

        # 상태 우선순위
        status_priority = case(
            (Simulation.status == "RUNNING", 1),
            (Simulation.status == "INITIATING", 2),
            (Simulation.status == "READY", 3),
            (Simulation.status == "PAUSED", 4),
            (Simulation.status == "FAILED", 5),
            (Simulation.status == "CANCELLED", 6),
            (Simulation.status == "COMPLETED", 7),
            else_=8
        )
        
        # 상태별 시간 기준
        time_priority = case(
            (Simulation.status == "RUNNING", Simulation.started_at),
            (Simulation.status == "INITIATING", Simulation.created_at),
            (Simulation.status == "READY", Simulation.created_at),
            (Simulation.status == "PAUSED", Simulation.updated_at),
            (Simulation.status == "FAILED", Simulation.updated_at),
            (Simulation.status == "CANCELLED", Simulation.updated_at),
            (Simulation.status == "COMPLETED", Simulation.completed_at),
            else_=Simulation.created_at
        )
        
        # 복합 우선순위 정렬
        query = query.order_by(status_priority, desc(time_priority))

        # 페이징 적용
        query = query.offset(pagination.offset).limit(pagination.limit)
        result = await self.db.execute(query)
        simulations = result.scalars().unique().all()

        return simulations

    async def count_all(
        self,
        pattern_type: Optional[PatternType] = None,
        status: Optional[SimulationStatus] = None
    ) -> int:
        """전체 시뮬레이션 개수 조회"""
        query = select(func.count(Simulation.id))
        if pattern_type:
            query = query.where(Simulation.pattern_type == pattern_type)
        if status:
            query = query.where(Simulation.status == status)
        result = await self.db.execute(query)
        return result.scalar_one()

    async def exists_by_id(self, simulation_id: int) -> bool:
        """시뮬레이션 존재 여부 확인"""
        stmt = select(Simulation).filter(Simulation.id == simulation_id)
        result = await self.db.execute(stmt)
        return result.scalars().first() is not None
    
    async def get_overview(self) -> Dict[int, int]:
        """시뮬레이션 전체 개요 조회 - 전체/상태별 개수"""
        stmt = select(
            func.count(Simulation.id).label('total'),
            func.sum(
                case((Simulation.status == SimulationStatus.READY, 1), else_=0)
            ).label('ready'),
            func.sum(
                case((Simulation.status == SimulationStatus.RUNNING, 1), else_=0)
            ).label('running'), 
            func.sum(
                case((Simulation.status == SimulationStatus.COMPLETED, 1), else_=0)
            ).label('completed'), 
            func.sum(
                case((Simulation.status == SimulationStatus.FAILED, 1), else_=0)
            ).label('failed')
        )
        
        result = await self.db.execute(stmt)
        row = result.first()
        
        return {
            'total': row.total,
            'ready': row.ready or 0,
            'running': row.running or 0,
            'completed': row.completed or 0,
            'failed': row.failed or 0
        }
    
    async def find_by_id(self, simulation_id: int) -> Simulation:
        """단일 시뮬레이션 조회"""
        stmt = select(Simulation).where(Simulation.id == simulation_id)
        result = await self.db.execute(stmt)
        simulation = result.scalars().first()
        return simulation
    
    async def find_steps_with_template(self, simulation_id: int) -> list[SimulationStep]:
        """
        Sequential 패턴용 Step 조회 + Template join
        """
        stmt = (
            select(SimulationStep)
            .options(selectinload(SimulationStep.template)) 
            .where(SimulationStep.simulation_id == simulation_id)
            .order_by(SimulationStep.step_order)
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()
    
    async def find_groups_with_template(self, simulation_id: int) -> list[SimulationGroup]:
        """
        Parallel 패턴용 Group 조회 + Template join
        """
        stmt = (
            select(SimulationGroup)
            .options(selectinload(SimulationGroup.template)) 
            .where(SimulationGroup.simulation_id == simulation_id)
        )
        
    async def find_simulation_steps(self, simulation_id: int) -> List[SimulationStep]:
        """
        simulation_id로 SimulationStep들을 step_order 순으로 조회
        
        Args:
            simulation_id: 조회할 시뮬레이션 ID
            
        Returns:
            List[SimulationStep]: step_order 순으로 정렬된 SimulationStep 목록
        """
        stmt = select(SimulationStep).where(
            SimulationStep.simulation_id == simulation_id
        ).order_by(SimulationStep.step_order)
        
        result = await self.db.execute(stmt)
        return result.scalars().all()
    
    async def find_simulation_groups(self, simulation_id: int) -> List[SimulationGroup]:
        """
        simulation_id로 SimulationGroup들을 id 기준 오름차순으로 조회
        
        Args:
            simulation_id: 조회할 시뮬레이션 ID
            
        Returns:
            List[SimulationGroup]: id 기준 오름차순으로 정렬된 SimulationStep 목록
        """
        stmt = select(SimulationGroup).where(
            SimulationGroup.simulation_id == simulation_id
        ).order_by(SimulationGroup.id)
        
        result = await self.db.execute(stmt)
        return result.scalars().all()
    
    async def update_simulation_status(self, simulation_id: int, status: str, failure_reason: Optional[str] = None):
        """
        simulation_id로 시뮬레이션의 상태를 업데이트
        
        Args:
            simulation_id: 업데이트할 시뮬레이션 ID
            status: 변경할 상태 값 (예: 'RUNNING', 'COMPLETED', 'FAILED')
            failure_reason: 실패 시 실패 원인 (선택적)
            
        Returns:
            bool: 업데이트 성공 여부
            
        Raises:
            ValueError: 시뮬레이션을 찾을 수 없는 경우
            SQLAlchemyError: 데이터베이스 오류 발생 시
        """
        try:
            # 현재 시간 설정
            current_time = datetime.now(timezone.utc)
            
            # 업데이트할 데이터 준비
            update_data = {
                "status": status
            }
            
            # 상태별 추가 필드 설정
            if status == "RUNNING":
                update_data["started_at"] = current_time
            elif status in ["COMPLETED", "FAILED"]:
                update_data["completed_at"] = current_time
                
            # 실패 원인이 있는 경우 추가
            #if failure_reason:
            #    update_data["failure_reason"] = failure_reason
            
            # 시뮬레이션 상태 업데이트 실행
            stmt = (
                update(Simulation)
                .where(Simulation.id == simulation_id)
                .values(**update_data)
            )
            
            result = await self.db.execute(stmt)
            
            # 업데이트된 행이 있는지 확인
            if result.rowcount == 0:
                raise ValueError(f"시뮬레이션 ID {simulation_id}를 찾을 수 없습니다.")
            
            # 변경사항 커밋
            await self.db.commit()
            
            print(f"✅ 시뮬레이션 {simulation_id} 상태 업데이트 완료: {status}")
            if failure_reason:
                print(f"   실패 원인: {failure_reason}")
                
            return True
            
        except ValueError:
            # 시뮬레이션을 찾을 수 없는 경우 롤백 후 재발생
            await self.db.rollback()
            raise
            
        except SQLAlchemyError as e:
            # 데이터베이스 오류 발생 시 롤백
            await self.db.rollback()
            print(f"❌ 데이터베이스 오류로 시뮬레이션 상태 업데이트 실패: {str(e)}")
            raise
            
        except Exception as e:
            # 기타 예상치 못한 오류 발생 시 롤백
            await self.db.rollback()
            print(f"❌ 예상치 못한 오류로 시뮬레이션 상태 업데이트 실패: {str(e)}")
            raise SQLAlchemyError(f"시뮬레이션 상태 업데이트 중 오류 발생: {str(e)}")