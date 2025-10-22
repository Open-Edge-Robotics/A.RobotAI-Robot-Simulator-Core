from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
import logging

logger = logging.getLogger(__name__)

class MecRepository:
    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session
    
    async def count_all(self) -> int:
        """전체 MEC 개수 조회"""
        try:
            if not self.db_session:
                logger.warning("DB 세션이 없어 임시 데이터를 반환합니다.")
                return 2
            
            # 임시 데이터 반환
            return 2
            
        except Exception as e:
            logger.error(f"전체 MEC 개수 조회 중 오류: {str(e)}")
            raise
        
# 의존성 주입을 위한 팩토리 함수
def create_mec_repository(db_session: Optional[AsyncSession] = None) -> MecRepository:
    """MEC 레포지토리 생성 팩토리"""
    return MecRepository(db_session)