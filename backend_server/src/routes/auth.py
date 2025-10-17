from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Header, Request, Response, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from crud.auth import AuthService, TokenData
from schemas.auth import SignUpRequest, LoginRequest, SigninData, SigninResponseModel, TokenResponseModel, UserData, UserResponseModel, VerifyTokenData, VerifyTokenResponseModel
from di.auth import get_auth_service
import logging

router = APIRouter(prefix="/auth", tags=["Auth"])
logger = logging.getLogger(__name__)

# ------------------------
# Swagger Authorize 버튼용 Security 객체
# ------------------------
bearer_scheme = HTTPBearer()


@router.post(
    "/signup",
    response_model=UserResponseModel,
    summary="회원가입",
    description="이메일, 비밀번호, 역할 정보를 입력받아 새 사용자를 생성합니다. 이메일 중복 시 409 오류 발생."
)
async def signup(req: SignUpRequest, service: Annotated[AuthService, Depends(get_auth_service)]):
    new_user: UserData = await service.signup(req.email, req.password, req.role)
    return UserResponseModel(
        status_code=status.HTTP_201_CREATED,
        data=new_user.model_dump(),
        message="회원가입이 완료되었습니다.",
    )

@router.post(
    "/signin",
    response_model=SigninResponseModel,
    summary="로그인",
    description="이메일과 비밀번호로 사용자 인증 후 Access Token과 Refresh Token을 발급합니다. Refresh Token은 HttpOnly 쿠키로 저장됩니다."
)
async def signin(
    res: Response,
    req: LoginRequest, 
    service: Annotated[AuthService, Depends(get_auth_service)]
):
    signin_data: SigninData = await service.signin(req.email, req.password, res)
    
    return SigninResponseModel(
        status_code=status.HTTP_200_OK,
        data=signin_data.model_dump(),
        message="로그인에 성공했습니다."
    )
    
@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="로그아웃",
    description="현재 로그인 상태에서 Refresh Token을 삭제하고 HttpOnly 쿠키를 제거합니다. 로그아웃 후 Access Token은 만료 전까지 유효합니다."
)
async def logout(
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)]
):
    await service.logout(request, response)
    
@router.post(
    "/refresh",
    response_model=TokenResponseModel,
    summary="액세스 토큰 재발급",
    description="쿠키에 저장된 Refresh Token을 기반으로 새로운 Access Token을 발급합니다. Refresh Token은 변경되지 않고 TTL만 연장됩니다."
)
async def refresh_token(
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)]
):
    token_data: TokenData = await service.refresh_access_token(request, response)
    return TokenResponseModel(
        status_code=status.HTTP_200_OK,
        data=token_data.model_dump(),
        message="토큰이 재발급되었습니다."
    )
    
@router.post(
    "/verify",
    response_model=VerifyTokenResponseModel,
    summary="액세스 토큰 검증",
    description="Authorization 헤더에 Bearer 스킴으로 전달된 Access Token을 검증합니다. 유효하면 사용자 정보와 토큰 유효 여부를 반환합니다."
)
async def verify_access_token(
    service: Annotated[AuthService, Depends(get_auth_service)],
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
):
    # Bearer 스킴 확인
    if credentials.scheme.lower() != "bearer":
        logger.warning(f"[VerifyAccessToken] 잘못된 인증 스킴 사용: {credentials.scheme}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="액세스 토큰이 올바르지 않습니다. 다시 로그인해주세요."
        )

    access_token = credentials.credentials
    verify_data: VerifyTokenData = await service.verify_access_token(access_token)
    return VerifyTokenResponseModel(
        status_code=status.HTTP_200_OK,
        data=verify_data.model_dump(),
        message="액세스 토큰이 유효합니다."
    )