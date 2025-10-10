import contextlib
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete
from database.db_conn import async_sessionmaker
from models.user import User
from models.refresh_token import RefreshToken
from models.enums import UserRole
from typing import Optional

class AuthRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        """AsyncSession Factory 주입"""
        self.session_factory = session_factory
        
    async def find_by_id(self, user_id: int, session: Optional[AsyncSession] = None) -> Optional[User]:
        """id 기반 User 조회"""
        manage_session = False
        if session is None:
            session = self.session_factory()
            manage_session = True
            
        async with session if manage_session else contextlib.nullcontext(session):
            stmt = select(User).where(User.id == user_id)
            result = await session.execute(stmt)
            return result.scalars().first()

    async def find_by_email(self, email: str, session: Optional[AsyncSession] = None) -> Optional[User]:
        """email 기반 User 조회"""
        manage_session = False
        if session is None:
            session = self.session_factory()
            manage_session = True
            
        async with session if manage_session else contextlib.nullcontext(session):
            stmt = select(User).where(User.email == email)
            result = await session.execute(stmt)
            return result.scalars().first()

    async def create_user(self, email: str, password: str, role: UserRole = UserRole.GENERAL, session: Optional[AsyncSession] = None) -> User:
        """새 사용자 생성 및 DB에 저장"""
        manage_session = False
        if session is None:
            session = self.session_factory()
            manage_session = True
            
        async with session if manage_session else contextlib.nullcontext(session):
            new_user = User(email=email, role=role)
            new_user.set_password(password)
            session.add(new_user)
            await session.commit()
            await session.refresh(new_user)
            return new_user
        
    async def create_refresh_token(self, user_id: int, refresh_token_str: str, expires_in_days: int) -> RefreshToken:
        """새 RefreshToken 생성 및 DB에 저장"""
        async with self.session_factory() as session:
            expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)
            refresh_token = RefreshToken(user_id=user_id, token=refresh_token_str, expires_at=expires_at)
            session.add(refresh_token)
            await session.commit()
            await session.refresh(refresh_token)
            return refresh_token
        
    async def find_refresh_token(self, user_id: Optional[int] = None, token: Optional[str] = None, session: Optional[AsyncSession] = None) -> Optional[RefreshToken]:
        """user_id 또는 token 기반 RefreshToken 조회"""
        manage_session = False
        if session is None:
            session = self.session_factory()
            manage_session = True

        async with session if manage_session else contextlib.nullcontext(session):
            stmt = select(RefreshToken).where(
                (RefreshToken.user_id == user_id) if user_id is not None else True,
                (RefreshToken.token == token) if token is not None else True
            )
            result = await session.execute(stmt)
            return result.scalars().first()
    
    async def delete_all_refresh_tokens(self, user_id: int, session: Optional[AsyncSession] = None) -> int:
        """user_id 기반 모든 RefreshToken 삭제, 삭제된 행 수 반환"""
        manage_session = False
        if session is None:
            session = self.session_factory()
            manage_session = True
            
        async with session if manage_session else contextlib.nullcontext(session):
            stmt = delete(RefreshToken).where(RefreshToken.user_id == user_id)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount
        
    async def save_refresh_token(self, refresh_token: RefreshToken, session: Optional[AsyncSession] = None) -> RefreshToken:
        """RefreshToken 엔티티 저장(업데이트)"""
        manage_session = False
        if session is None:
            session = self.session_factory()
            manage_session = True
            
        async with session if manage_session else contextlib.nullcontext(session):
            session.add(refresh_token)
            await session.commit()
            await session.refresh(refresh_token)
            return refresh_token
    
