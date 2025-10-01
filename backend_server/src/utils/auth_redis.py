from datetime import datetime, timedelta, timezone
import json
from typing import Optional
from database.redis_conn import RedisConnection
import logging

logger = logging.getLogger(__name__)

class AuthRedisClient:
    """Refresh Token 관리 전용 RedisClient"""
    def __init__(self, redis_connection: RedisConnection):
        self.redis = redis_connection.client
        
    # ------------------------------
    # Redis 키 생성 메서드
    # ------------------------------
    def _refresh_token_key(self, user_id: int) -> str:
        """
        Redis에 저장된 Refresh Token 키 반환
        """
        return f"refresh:{user_id}"
    
    def _user_tokens_key(self, user_id: int) -> str:
        """
        특정 사용자의 모든 토큰을 관리하는 키 반환
        """
        return f"user:{user_id}:refresh_tokens"

    # ------------------------------
    # 저장
    # ------------------------------
    async def store_refresh_token(
        self,
        user_id: int,
        refresh_token: str,
        data: dict,
        expiry: timedelta,
    ) -> None:
        """refresh:{token} 저장 + user:{id}:refresh_tokens에 토큰 등록"""
        key = self._refresh_token_key(refresh_token)
        user_tokens_key = self._user_tokens_key(user_id)

        # 단일 토큰 저장 (TTL 적용)
        await self.redis.setex(key, int(expiry.total_seconds()), json.dumps(data))

        # 유저별 토큰 Set에 추가
        await self.redis.sadd(user_tokens_key, refresh_token)
        
    # ------------------------------
    # 조회
    # ------------------------------
    async def get_refresh_token(self, refresh_token: str) -> Optional[dict]:
        key = self._refresh_token_key(refresh_token)
        raw = await self.redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    # ------------------------------
    # 삭제 (단일 로그아웃)
    # ------------------------------
    async def delete_refresh_token(self, user_id: int, refresh_token: str) -> bool:
        key = self._refresh_token_key(refresh_token)
        user_tokens_key = self._user_tokens_key(user_id)

        try:
            # 두 곳 모두 삭제
            deleted = await self.redis.delete(key)
            await self.redis.srem(user_tokens_key, refresh_token)
            return deleted > 0
        except Exception as e:
            logger.warning(f"[Redis] refresh_token 삭제 실패 user_id={user_id}, token={refresh_token}, error={e}")
            return False
        
    # ------------------------------
    # 전체 삭제 (강제 로그아웃)
    # ------------------------------
    async def delete_all_refresh_tokens(self, user_id: int) -> None:
        user_tokens_key = self._user_tokens_key(user_id)
        tokens = await self.redis.smembers(user_tokens_key)

        if tokens:
            keys = [self._refresh_token_key(t.decode()) for t in tokens]
            await self.redis.delete(*keys)

        await self.redis.delete(user_tokens_key)
        
    # ------------------------------
    # 만료 연장
    # ------------------------------
    async def extend_refresh_token(self, refresh_token: str, new_expiry: datetime):
        key = self._refresh_token_key(refresh_token)

        # 토큰 존재 여부 확인
        if not await self.redis.exists(key):
            raise ValueError("Redis에 Refresh Token이 존재하지 않습니다.")

        # new_expiry UTC 변환
        if new_expiry.tzinfo is None:
            new_expiry = new_expiry.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        ttl_seconds = int((new_expiry - now).total_seconds())
        if ttl_seconds <= 0:
            raise ValueError("새 만료 시간이 이미 지난 시간입니다.")

        await self.redis.expire(key, ttl_seconds)