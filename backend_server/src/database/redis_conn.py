import os
import aioredis

class RedisConnection:
    """Redis 싱글톤 연결 객체"""
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
        self._initialized = True

    async def connect(self):
        """Redis 연결 초기화"""
        if self.client is None:
            self.client = aioredis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
