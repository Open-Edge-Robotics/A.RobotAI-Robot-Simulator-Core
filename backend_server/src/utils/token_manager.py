import jwt
from datetime import datetime, timedelta, timezone

class TokenManager:
    def __init__(
        self, 
        secret_key: str, 
        algorithm: str,
        access_exp_minutes: int = 15,
        refresh_exp_days: int = 7
    ):
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.access_token_expire_minutes = access_exp_minutes
        self.refresh_token_expire_days = refresh_exp_days

    def create_access_token(self, user_id: int, email: str, role: str) -> str:
        expire = datetime.now(timezone.utc) + timedelta(minutes=self.access_token_expire_minutes)
        payload = {"sub": str(user_id), "email": email, "role": role, "exp": expire}
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def create_refresh_token(self, user_id: int) -> str:
        expire = datetime.now(timezone.utc) + timedelta(days=self.refresh_token_expire_days)
        payload = {"sub": str(user_id), "exp": expire}
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def decode_token(self, token: str) -> dict:
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return payload
        except jwt.ExpiredSignatureError:
            raise Exception("토큰 만료")
        except jwt.InvalidTokenError:
            raise Exception("유효하지 않은 토큰")
