import contextlib
from typing import Annotated, Optional
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
import logging

from database.db_conn import get_async_sessionmaker
from models.template import Template

logger = logging.getLogger(__name__)

class TemplateRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        """AsyncSession Factory 주입"""
        self.session_factory = session_factory
    
    async def find_by_id(self, template_id: int, session: Optional[AsyncSession] = None) -> Optional[Template]:
        manage_session = False
        if session is None:
            session = self.session_factory()
            manage_session = True

        async with session if manage_session else contextlib.nullcontext(session):
            try:
                stmt = select(Template).where(Template.template_id == template_id)
                result = await session.execute(stmt)
                return result.scalars().first()
            except Exception as e:
                logger.error(f"Template 조회 중 오류: {str(e)}")
                return None

        
# Repository 생성 팩토리
def create_template_repository(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_async_sessionmaker)]
) -> TemplateRepository:
    """AsyncSession Factory 기반 안전한 Repository 생성"""
    return TemplateRepository(session_factory)
