from fastapi import HTTPException as StarletteHTTPException, Request, FastAPI
from fastapi.exceptions import RequestValidationError
from kubernetes.client import ApiException
from minio import S3Error
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse
import logging

from exception.simulation_exceptions import (
    SimulationError,
    SimulationNotFoundError,
    SimulationStepNotFoundError,
    SimulationGroupNotFoundError,
    PatternTypeMismatchError,
    SimulationStatusError,
)
from exception.template_exceptions import TemplateError
from schemas.format import GlobalResponseModel

app = FastAPI()
logger = logging.getLogger(__name__)


# -----------------------------
# 헬퍼 함수
# -----------------------------
def safe_status_code(value, default=500):
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return int(value)
        except (ValueError, TypeError):
            return default
    if isinstance(value, int):
        return value
    return default


def safe_create_response(status_code, data, message, ui_message=None):
    """
    GlobalResponseModel을 안전하게 생성
    - status_code 500인 경우 UI용 메시지를 친화적으로 표시
    - 실제 로깅용 메시지는 message 그대로 사용
    """
    try:
        if status_code == 500:
            ui_message = ui_message or "예기치 못한 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
        else:
            ui_message = message

        logger.error(f"Response {status_code}: {message}")

        return GlobalResponseModel(
            status_code=safe_status_code(status_code),
            data=data,
            message=ui_message
        )
    except Exception as e:
        logger.error(f"Error creating GlobalResponseModel: {e}")
        return GlobalResponseModel(
            status_code=500,
            data=None,
            message="예기치 못한 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
        )


# -----------------------------
# 공통 핸들러 팩토리
# -----------------------------
def create_exception_handler(status_code: int):
    async def handler(request: Request, exc: Exception):
        logger.error(f"{type(exc).__name__}: {str(exc)}")
        response = safe_create_response(
            status_code=status_code,
            data=None,
            message=str(exc)
        )
        return JSONResponse(status_code=status_code, content=response.model_dump())
    return handler


def create_s3_handler(default_status=500):
    async def handler(request: Request, exc: S3Error):
        logger.error(f"S3Error: code={getattr(exc, 'code', None)}, message={getattr(exc, 'message', None)}")
        s3_code = getattr(exc, 'code', None) or default_status
        response = safe_create_response(
            status_code=safe_status_code(s3_code, default_status),
            data=None,
            message=str(exc)
        )
        return JSONResponse(status_code=safe_status_code(s3_code, default_status), content=response.model_dump())
    return handler


def create_api_exception_handler(default_status=500):
    async def handler(request: Request, exc: ApiException):
        logger.error(f"ApiException: status={getattr(exc, 'status', None)}, reason={getattr(exc, 'reason', None)}")
        response = safe_create_response(
            status_code=safe_status_code(default_status),
            data=None,
            message=str(exc)
        )
        return JSONResponse(status_code=safe_status_code(default_status), content=response.model_dump())
    return handler


def create_validation_exception_handler():
    async def handler(request: Request, exc: RequestValidationError):
        logger.error(f"RequestValidationError: {exc.errors()}")
        response = safe_create_response(
            status_code=400,
            data=None,
            message={"path": request.url.path, "method": request.method, "error_message": exc.errors()}
        )
        return JSONResponse(status_code=400, content=response.model_dump())
    return handler


# -----------------------------
# 핸들러 등록
# -----------------------------
def register_exception_handlers(app: FastAPI):
    app.add_exception_handler(StarletteHTTPException, create_exception_handler(status_code=500))
    app.add_exception_handler(S3Error, create_s3_handler())
    app.add_exception_handler(ApiException, create_api_exception_handler())
    app.add_exception_handler(RequestValidationError, create_validation_exception_handler())
    app.add_exception_handler(SQLAlchemyError, create_exception_handler(500))
    app.add_exception_handler(Exception, create_exception_handler(500))

    # -----------------------------
    # SimulationError 하위 예외
    # -----------------------------
    app.add_exception_handler(SimulationNotFoundError, create_exception_handler(404))
    app.add_exception_handler(SimulationStepNotFoundError, create_exception_handler(404))
    app.add_exception_handler(SimulationGroupNotFoundError, create_exception_handler(404))
    app.add_exception_handler(PatternTypeMismatchError, create_exception_handler(400))
    app.add_exception_handler(SimulationStatusError, create_exception_handler(409))

    # TemplateError
    app.add_exception_handler(TemplateError, create_exception_handler(400))
