import os
import aioredis
import json

class RedisSimulationClient:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return
        host = os.getenv("REDIS_HOST", "redis.robot.svc.cluster.local")
        port = int(os.getenv("REDIS_PORT", 6379))
        password = os.getenv("REDIS_PASSWORD", "redisPass123")
        db = int(os.getenv("REDIS_DB", 0))
        self.redis_url = f"redis://{f':{password}@' if password else ''}{host}:{port}/{db}"
        self.client: aioredis.Redis | None = None
        self.ttl = 24 * 3600  # 기본 TTL 24시간
        self._initialized = True

    async def connect(self):
        """Redis 연결 초기화"""
        self.client = aioredis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)

    async def set_simulation_status(self, simulation_id: int, value: dict, ttl: int | None = None):
        """시뮬레이션 진행상황을 Redis에 저장"""
        if not self.client:
            await self.connect()
        key = f"simulation:{simulation_id}"
        await self.client.set(key, json.dumps(value))
        if ttl or self.ttl:
            await self.client.expire(key, ttl or self.ttl)

    async def get_simulation_status(self, simulation_id: int) -> dict | None:
        """Redis에서 시뮬레이션 진행상황 조회"""
        if not self.client:
            await self.connect()
        key = f"simulation:{simulation_id}"
        data = await self.client.get(key)
        return json.loads(data) if data else None

    async def delete_simulation_status(self, simulation_id: int):
        """Redis에서 시뮬레이션 진행상황 삭제"""
        if not self.client:
            await self.connect()
        key = f"simulation:{simulation_id}"
        await self.client.delete(key)
        
    async def health_check(self) -> bool:
        """
        Redis health check
        - 연결 상태 확인
        - PING 호출로 간단 확인
        """
        try:
            await self.connect()
            pong = await self.client.ping()
            return pong is True
        except Exception as e:
            print(f"Redis health check failed: {e}")
            return False
