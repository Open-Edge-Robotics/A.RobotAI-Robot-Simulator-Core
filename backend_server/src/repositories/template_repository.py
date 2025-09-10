from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
import logging

from models.template import Template

logger = logging.getLogger(__name__)

class TemplateRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        """AsyncSession Factory 주입"""
        self.session_factory = session_factory
        
    async def find_by_id(self, template_id: int) -> Optional[Template]:
        try:
            async with self.session_factory() as session:
                stmt = select(Template).where(Template.id == template_id)
                result = await session.execute(stmt)
                return result.scalars().first()
        except Exception as e:
            logger.error(f"단일 시뮬레이션 조회 중 오류: {str(e)}")
            return None
        
# Repository 생성 팩토리
def create_template_repository(session_factory: async_sessionmaker[AsyncSession]) -> SimulationRepository:
    """AsyncSession Factory 기반 안전한 Repository 생성"""
    return TemplateRepository(session_factory)
