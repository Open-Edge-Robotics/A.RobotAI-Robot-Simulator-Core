from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from models.instance import Instance

logger = logging.getLogger(__name__)

class InstanceRepository:
    def __init__(self, db_session: Optional[AsyncSession] = None):
        self.db_session = db_session
    
    async def count_all(self) -> int:
        """전체 인스턴스 개수 조회"""
        try:
            if not self.db_session:
                logger.warning("DB 세션이 없어 임시 데이터를 반환합니다.")
                return 24
            
            # 실제 DB 쿼리 (모델이 구현되면 주석 해제)
            result = await self.db_session.execute(
                select(func.count(Instance.id))
            )
            return result.scalar() or 0
        except Exception as e:
            logger.error(f"전체 인스턴스 개수 조회 중 오류: {str(e)}")
            raise

# 의존성 주입을 위한 팩토리 함수
def create_instance_repository(db_session: Optional[AsyncSession] = None) -> InstanceRepository:
    """인스턴스 레포지토리 생성 팩토리"""
    return InstanceRepository(db_session)