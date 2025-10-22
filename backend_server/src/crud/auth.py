from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from sqlalchemy import delete
from fastapi import HTTPException, Request, Response, status
from utils.token_manager import TokenManager
from repositories.auth_repository import AuthRepository
from schemas.auth import SigninData, TokenData, UserData, VerifyTokenData
from schemas.auth import UserData
from utils.auth_redis import AuthRedisClient
from models.refresh_token import RefreshToken
from models.enums import UserRole
from settings import settings
import logging

logger = logging.getLogger(__name__)

class AuthService:
    def __init__(self, auth_repository: AuthRepository, auth_redis: AuthRedisClient, token_manager: TokenManager):
        self.auth_repository = auth_repository
        self.auth_redis = auth_redis
        self.token_manager = token_manager

    async def signup(self, email: str, password: str, role: UserRole = UserRole.GENERAL) -> UserData:
        """새 사용자 생성 및 DB에 저장"""
        existing_user = await self.auth_repository.find_by_email(email)
        if existing_user:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 사용 중인 이메일입니다.")
        
        new_user = await self.auth_repository.create_user(email, password, role)

        return UserData.from_entity(new_user)

    async def signin(self, email: str, password: str, response: Response) -> SigninData:
        """사용자 인증 후 액세스 토큰과 리프레시 토큰 발급"""
        user = await self.auth_repository.find_by_email(email)
        print(f"user={user}")
        if not user or not user.verify_password(password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인 정보가 올바르지 않습니다. 다시 시도해주세요.")

        access_token, refresh_token_str = await self._generate_tokens(user.id, user.email, user.role)
        
        # Refresh Token → HttpOnly 쿠키에 저장
        response.set_cookie(
            key="refresh_token",
            value=refresh_token_str,
            httponly=True,
            secure=False,  # 개발 환경에서는 False, 운영 환경에서는 True로 설정
            samesite="strict",
            path="/api",  # refresh 요청에서만 쿠키 전송
            max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
        )
        
        """TokenData 에 expires_in 대신 expires_at 추가"""
        expires_at = datetime.now(tz=timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

        return SigninData(
            email=user.email,
            access_token=access_token,
            expires_at=expires_at.isoformat(timespec='seconds').replace("+00:00", "Z"),
            role=user.role
        )

    async def logout(self, request: Request, response: Response):
        """
        단일 기기 로그아웃
        - DB: 해당 refresh_token 삭제
        - Redis: refresh:{token} 삭제 + user:{id}:refresh_tokens에서 제거
        """
        logger.info("[Logout] 로그아웃 요청 시작")
        
        # Request에서 쿠키 추출
        refresh_token = request.cookies.get("refresh_token")
        if not refresh_token:
            logger.warning("[Logout] 쿠키에서 refresh_token을 찾을 수 없음")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그아웃 정보를 확인할 수 없습니다. 다시 시도해주세요.")
        logger.debug(f"[Logout] 쿠키에서 refresh_token 추출 성공: {refresh_token[:10]}***")

        # Refresh Token 검증
        try:
            payload = self.token_manager.decode_token(refresh_token)
            user_id = payload.get("sub")
            if not user_id:
                raise ValueError("토큰에 user_id(sub) 없음")
            user_id = int(user_id)
            logger.debug(f"[Logout] 토큰 검증 성공, user_id={user_id}")
        except Exception as e:
            logger.error(f"[Logout] 토큰 검증 실패: {e}")
            raise HTTPException(status_code=401, detail="로그아웃 세션 정보가 유효하지 않습니다. 다시 시도해주세요.")

        # DB에서 리프레시 토큰 삭제 (한 번만)
        deleted_count = await self.auth_repository.delete_all_refresh_tokens(user_id=user_id)
        logger.info(f"[Logout] DB에서 삭제된 refresh_token 수: {deleted_count}, user_id={user_id}")
            
        # Redis에서도 삭제 (예외 처리)
        try:
            deleted_in_redis = await self.auth_redis.delete_refresh_token(user_id, refresh_token)
            if deleted_in_redis:
                logger.info(f"[Logout] Redis에서 refresh_token 삭제 성공, user_id={user_id}")
            else:
                logger.info(f"[Logout] Redis에서 삭제된 토큰 없음 또는 이미 삭제됨, user_id={user_id}")
        except Exception as e:
            logger.error(f"[Logout] Redis 삭제 중 예외 발생: {e}, user_id={user_id}")
            
        # HttpOnly 쿠키 삭제
        response.delete_cookie(key="refresh_token", path="/api/auth/refresh")
        logger.debug(f"[Logout] HttpOnly 쿠키 삭제 완료, user_id={user_id}")

        # 로그 기록    
        logger.info(f"[Logout] user_id={user_id} 로그아웃 완료")
        
    async def refresh_access_token(self, request: Request, response: Response) -> TokenData:
        """
        쿠키에 있는 refresh_token 기반으로 Access Token 재발급
        """
        refresh_token = request.cookies.get("refresh_token")
        if not refresh_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="세션 정보가 없습니다. 다시 로그인해주세요.")

        # 1️⃣ 토큰 decode → user_id 추출
        try:
            payload = self.token_manager.decode_token(refresh_token)
            user_id = payload.get("sub")
            if not user_id:
                raise ValueError("토큰에 user_id(sub) 없음")
            user_id = int(user_id)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="세션이 유효하지 않습니다. 다시 로그인해주세요.")

        # 2️⃣ DB 검증
        user = await self.auth_repository.find_by_id(user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="사용자 정보를 찾을 수 없습니다. 다시 로그인해주세요.")
        
        email = user.email
        role = user.role
        
        stored_refresh_token = await self.auth_repository.find_refresh_token(user_id=user_id, token=refresh_token)
        if not stored_refresh_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="세션이 만료되었거나 유효하지 않습니다. 다시 로그인해주세요")

        # 3️⃣ Redis 검증 (이제는 refresh_token 단위 조회)
        token_data = await self.auth_redis.get_refresh_token(refresh_token)
        if not token_data or token_data["user_id"] != user_id:
            raise HTTPException(status_code=401, detail="세션 정보가 유효하지 않습니다. 다시 로그인해주세요")

        # 4️⃣ 만료 연장 (DB + Redis 둘 다)
        stored_refresh_token.extend_expiry(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)  # DB 도메인 메서드
        await self.auth_repository.save_refresh_token(stored_refresh_token)          # 변경 반영

        new_expiry = stored_refresh_token.expires_at
        await self.auth_redis.extend_refresh_token(refresh_token, new_expiry)  # Redis 갱신

        # 5️⃣ 새 Access Token 발급
        access_token, expires_at = await self._generate_access_token(user_id, email, role)

        # 6️⃣ JSON 응답
        return TokenData(
            access_token=access_token,
            expires_at=expires_at,
        )
            
    async def verify_access_token(self, token: str) -> VerifyTokenData:
        """
        Authorization 헤더에서 전달된 Access Token 검증 후 DTO 반환
        """
        try:
            payload = self.token_manager.decode_token(token)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="액세스 토큰이 유효하지 않습니다. 다시 로그인해주세요."
            )

        exp_timestamp = payload.get("exp")
        if not exp_timestamp:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="액세스 토큰이 유효하지 않습니다. 다시 로그인해주세요."
            )

        now = datetime.now(timezone.utc).timestamp()
        if now > exp_timestamp:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="액세스 토큰이 만료되었습니다. 다시 로그인해주세요."
            )

        user_id = payload.get("sub")
        email = payload.get("email")
        role = payload.get("role")

        if not user_id or not email or not role:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="액세스 토큰이 유효하지 않습니다. 다시 로그인해주세요."
            )

        return VerifyTokenData(
            is_valid=True,
            user_id=int(user_id),
            email=email,
            role=role
        )
    
    # -----------------------------
    # 토큰 발급 공통
    # -----------------------------
    async def _generate_tokens(self, user_id: int, email: str, role: UserRole) -> Tuple[str, str]:
        access_token = self.token_manager.create_access_token(user_id, email, role.value)
        refresh_token_str = self.token_manager.create_refresh_token(user_id)

        # DB에 저장
        await self.auth_repository.create_refresh_token(
            user_id, refresh_token_str, settings.REFRESH_TOKEN_EXPIRE_DAYS
        )
        
        # Redis에 저장
        ttl_seconds = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
        token_data = {
            "user_id": user_id,
            "email": email,
            "role": role, 
            "issued_at": datetime.now(timezone.utc).isoformat()
        }
        await self.auth_redis.store_refresh_token(user_id, refresh_token_str, token_data, timedelta(seconds=ttl_seconds))
        
        return access_token, refresh_token_str
    
    async def _generate_access_token(self, user_id: int, email: str, role: UserRole) -> Tuple[str, str]:
        """
        Access Token만 발급
        Returns:
            access_token (str)
            expires_in (int, seconds)
            expires_at (str, ISO 8601 UTC)
        """
        # 1️⃣ Access Token 발급
        access_token = self.token_manager.create_access_token(user_id, email, role.value)

        # 2️⃣ 만료 시간 계산
        expires_in = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        expires_at_dt = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        expires_at = expires_at_dt.isoformat(timespec='seconds').replace("+00:00", "Z")

        return access_token, expires_at