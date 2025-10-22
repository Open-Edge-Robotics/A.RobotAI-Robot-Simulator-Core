from typing import Annotated
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from settings import settings
from di.redis import get_auth_redis_client
from database.db_conn import get_async_sessionmaker
from repositories.auth_repository import AuthRepository
from crud.auth import AuthRedisClient, AuthService, TokenManager

def get_token_manager() -> TokenManager:
    return TokenManager(
        secret_key=settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
        access_exp_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        refresh_exp_days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )

def get_auth_repository(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_async_sessionmaker)]
) -> AuthRepository:
    return AuthRepository(session_factory)

def get_auth_service(
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    auth_redis: Annotated[AuthRedisClient, Depends(get_auth_redis_client)],
    token_manager: Annotated[TokenManager, Depends(get_token_manager)]
) -> AuthService:
    return AuthService(auth_repository, auth_redis, token_manager)

