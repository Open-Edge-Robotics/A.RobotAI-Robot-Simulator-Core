from typing import Annotated
from fastapi import Depends
from database.redis_conn import RedisConnection
from utils.auth_redis import AuthRedisClient

redis_connection = RedisConnection()

async def get_redis_connection() -> RedisConnection:
    if redis_connection.client is None:
        await redis_connection.connect()
    return redis_connection

async def get_auth_redis_client(redis_conn: Annotated[RedisConnection, Depends(get_redis_connection)]) -> AuthRedisClient:
    return AuthRedisClient(redis_conn)
