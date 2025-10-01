from pydantic import BaseModel, EmailStr, Field, ValidationInfo, field_validator
from typing import Optional

from sqlmodel import values
from schemas.format import BaseSchema, GlobalResponseModel
from models.enums import UserRole

# 회원가입 요청
class SignUpRequest(BaseModel):
    email: EmailStr
    password: str
    password_confirm: str
    role: str
    
    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("비밀번호는 최소 8자 이상이어야 합니다.")
        if len(v) > 32:
            raise ValueError("비밀번호는 최대 32자 이하여야 합니다.")
        if ' ' in v:
            raise ValueError("비밀번호에 공백을 포함할 수 없습니다.")
        return v
    
    """TypeError: argument of type 'pydantic_core._pydantic_core.ValidationInfo' is not iterable 에러 발생했어. 어떻게 해결해?"""
    @field_validator('password_confirm')
    @classmethod
    def passwords_match(cls, v, info: ValidationInfo):
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("passwordConfirm이 password와 일치하지 않습니다.")
        return v
    
    @field_validator('role')
    @classmethod
    def valid_role(cls, v):
        if v not in (UserRole.ADMIN.value, UserRole.GENERAL.value):
            raise ValueError("role은 'admin' 또는 'general'만 가능합니다.")
        return v
    
# 로그인 요청
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("비밀번호는 최소 8자 이상이어야 합니다.")
        return v

    @field_validator('email')
    @classmethod
    def validate_email(cls, v):
        if not v:
            raise ValueError("이메일은 필수 항목입니다.")
        if len(v) < 5 or len(v) > 254:
            raise ValueError("이메일은 5자 이상 254자 이하여야 합니다.")
        return v
    
# 회원가입 및 로그인 응답
class UserData(BaseModel):
    email: EmailStr
    role: UserRole
    
    def from_entity(user) -> "UserData":
        return UserData(
            email=user.email,
            role=user.role
        )

class UserResponseModel(GlobalResponseModel[UserData]):
    data: Optional[UserData] = None
    
    class Config:
        schema_extra = {
            "example": {
                "statusCode": 200,
                "data": {
                    "email": "user@example.com",
                    "role": "general"
                },
                "message": "회원가입이 완료되었습니다."
            }
        }

class SigninData(BaseSchema):
    email: EmailStr = Field(..., alias="email")
    access_token: str = Field(..., alias="accessToken")
    expires_at: str = Field(..., alias="expiresAt")
    role: UserRole = Field(..., alias="role")

class SigninResponseModel(GlobalResponseModel[SigninData]):
    data: Optional[SigninData] = None

    class Config:
        schema_extra = {
            "example": {
                "statusCode": 200,
                "data": {
                    "email": "user@example.com",
                    "accessToken": "eyJhbGciOi...",
                    "expiresAt": "2023-01-01T00:00:00Z",
                    "role": "general"
                },
                "message": "로그인에 성공했습니다."
            }
        }


class TokenData(BaseSchema):
    access_token: str = Field(..., alias="accessToken")
    expires_at: str = Field(..., alias="expiresAt")
    
class TokenResponseModel(GlobalResponseModel[TokenData]):
    data: Optional[TokenData] = None

    class Config:
        schema_extra = {
            "example": {
                "statusCode": 200,
                "data": {
                    "accessToken": "eyJhbGciOi...",
                    "expiresAt": "2023-01-01T00:00:00Z"
                },
                "message": "토큰이 재발급되었습니다."
            }
        }
        
# ------------------------
# Verify Token Response Data
# ------------------------
class VerifyTokenData(BaseSchema):
    is_valid: bool = Field(..., alias="isValid")
    user_id: int = Field(..., alias="userId")
    email: EmailStr = Field(..., alias="email")
    role: UserRole = Field(..., alias="role")
    
class VerifyTokenResponseModel(GlobalResponseModel[VerifyTokenData]):
    data: Optional[VerifyTokenData] = None

    class Config:
        schema_extra = {
            "example": {
                "statusCode": 200,
                "data": {
                    "isValid": True,
                    "userId": 1,
                    "email": "user@example.com",
                    "role": "general"
                },
                "message": "토큰이 유효합니다."
            }
        }