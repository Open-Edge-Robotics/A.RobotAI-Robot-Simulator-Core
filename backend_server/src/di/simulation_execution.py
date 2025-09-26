from fastapi import Depends

from crud.simulation_execution import SimulationExecutionRepository, SimulationExecutionService
from database.db_conn import AsyncSession, async_sessionmaker, get_async_sessionmaker
from database.redis_simulation_client import RedisSimulationClient

# Repository 의존성
def get_simulation_execution_repository(
    session_factory:async_sessionmaker[AsyncSession] = Depends(get_async_sessionmaker)    
) -> SimulationExecutionRepository:
    return SimulationExecutionRepository(session_factory)

# ------------------------------
# Redis 클라이언트 의존성
# ------------------------------
async def get_redis_client() -> RedisSimulationClient:
    client = RedisSimulationClient()
    await client.connect()
    return client

# Service 의존성
def get_simulation_execution_service(
    repo: SimulationExecutionRepository = Depends(get_simulation_execution_repository),
    redis_client: RedisSimulationClient = Depends(get_redis_client)
) -> SimulationExecutionService:
    return SimulationExecutionService(repo, redis_client)
