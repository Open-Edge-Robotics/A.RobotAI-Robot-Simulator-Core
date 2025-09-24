import contextlib
from typing import Annotated, List, Optional, AsyncContextManager
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
import logging

from exception.template_exceptions import TemplateNotFoundError
from schemas.template import TemplateCreateRequest, TemplateUpdateRequest
from database.db_conn import get_async_sessionmaker
from models.template import Template

logger = logging.getLogger(__name__)

class TemplateRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        """AsyncSession Factory 주입"""
        self.session_factory = session_factory
        
    def _get_session_context(self, session: Optional[AsyncSession]) -> tuple[AsyncSession, AsyncContextManager]:
        """세션 컨텍스트 관리 헬퍼"""
        if session is not None:
            # 외부에서 제공된 세션 사용 (트랜잭션 관리는 외부에서)
            return session, contextlib.nullcontext(session)
        else:
            # 내부에서 세션 생성 (자체 트랜잭션 관리)
            new_session = self.session_factory()
            return new_session, new_session
    
    async def find_by_id(self, template_id: int, session: Optional[AsyncSession] = None) -> Optional[Template]:
        current_session, session_context = self._get_session_context(session)

        async with session_context:
                stmt = select(Template).where(Template.deleted_at.is_(None)).where(Template.template_id == template_id)
                result = await current_session.execute(stmt)
                return result.scalars().first()
            
    async def find_by_name(self, template_name: str, session: Optional[AsyncSession] = None) -> Optional[Template]:
        current_session, session_context = self._get_session_context(session)

        async with session_context:
                stmt = select(Template).where(Template.deleted_at.is_(None)).where(Template.name == template_name)
                result = await current_session.execute(stmt)
                return result.scalars().first()
    
    async def find_all(self, session: Optional[AsyncSession] = None) -> List[Template]:
        current_session, session_context = self._get_session_context(session)
        async with session_context:
            stmt = select(Template).where(Template.deleted_at.is_(None)).order_by(Template.template_id.desc())
            result = await current_session.execute(stmt)
            return result.scalars().all()
        
    async def create_template(self, template_data: TemplateCreateRequest, session: Optional[AsyncSession] = None) -> Template:
        """템플릿 생성 - 쓰기 작업이므로 커밋/롤백 필요"""
        current_session, session_context = self._get_session_context(session)
        
        async with session_context:
            try:
                new_template = Template(
                    name=template_data.name,
                    type=template_data.type,
                    description=template_data.description,
                    bag_file_path=template_data.bag_file_path,
                    topics=template_data.topics,
                )
                current_session.add(new_template)
                
                # 자체 세션인 경우에만 커밋
                if session is None:
                    await current_session.commit()
                    await current_session.refresh(new_template)
                
                return new_template
                
            except Exception as e:
                logger.error(f"Template 생성 중 오류: {str(e)}")
                if session is None:
                    await current_session.rollback()
                raise
    
    async def delete_template(self, template_id: int, session: Optional[AsyncSession] = None) -> int:
        """템플릿 삭제 - 쓰기 작업이므로 커밋/롤백 필요"""
        current_session, session_context = self._get_session_context(session)
        
        async with session_context:
            try:
                # 먼저 조회 (같은 세션 사용)
                template = await self.find_by_id(template_id, current_session)
                if not template:
                    raise TemplateNotFoundError(template.id)
                
                # 삭제
                await current_session.delete(template)
                
                # 자체 세션인 경우에만 커밋
                if session is None:
                    await current_session.commit()
                
                return template_id
                
            except Exception as e:
                logger.error(f"Template 삭제 중 오류: {str(e)}")
                if session is None:
                    await current_session.rollback()
                raise
    
    async def update_template(self, template_id: int, template_data: TemplateUpdateRequest, session: Optional[AsyncSession] = None):
        current_session, session_context = self._get_session_context(session)
        async with session_context:
            try:
                # 먼저 조회 (같은 세션 사용)
                existing_template = await self.find_by_id(template_id, current_session)
                if not existing_template:
                    raise TemplateNotFoundError(existing_template.id)
                    
                
                # 필드 업데이트
                if template_data.name is not None: 
                    existing_template.name = template_data.name
                if template_data.type is not None:
                    existing_template.type = template_data.type 
                if template_data.description is not None:
                    existing_template.description = template_data.description
                if template_data.topics is not None:
                    existing_template.topics = template_data.topics
                
                
                # 자체 세션인 경우에만 커밋
                if session is None:
                    await current_session.commit()
                    await current_session.refresh(existing_template)
                    
                # updated_at 필드가 있다면 업데이트
                if hasattr(existing_template, 'updated_at'):
                    from datetime import datetime
                    existing_template.updated_at = datetime.now()
                
                return existing_template
                
            except Exception as e:
                logger.error(f"Template 업데이트 중 오류: {str(e)}")
                # 자체 세션인 경우에만 롤백
                if session is None:
                    await current_session.rollback()
                raise

        
# Repository 생성 팩토리
def create_template_repository(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_async_sessionmaker)]
) -> TemplateRepository:
    """AsyncSession Factory 기반 안전한 Repository 생성"""
    return TemplateRepository(session_factory)
