from fastapi import HTTPException as StarletteHTTPException
from fastapi import Request, FastAPI
from fastapi.exceptions import RequestValidationError
from kubernetes.client import ApiException
from minio import S3Error
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse
import logging

from schemas.format import GlobalResponseModel

app = FastAPI()

# 로거 설정
logger = logging.getLogger(__name__)


def safe_status_code(value, default=500):
    """status_code를 안전하게 처리하는 헬퍼 함수"""
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


def safe_create_response(status_code, data, message):
    """GlobalResponseModel을 안전하게 생성하는 헬퍼 함수"""
    try:
        return GlobalResponseModel(
            status_code=safe_status_code(status_code),
            data=data,
            message=message,
        )
    except Exception as e:
        logger.error(f"Error creating GlobalResponseModel: {e}")
        # Fallback response
        return GlobalResponseModel(
            status_code=500,
            data=None,
            message="Internal Server Error",
        )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exception: StarletteHTTPException):
    """HTTPException 처리 핸들러"""
    logger.error(f"HTTPException: status_code={exception.status_code}, detail={exception.detail}")

    response = safe_create_response(
        status_code=exception.status_code,
        data=None,
        message=str(exception.detail),
    )
    return JSONResponse(status_code=safe_status_code(exception.status_code), content=response.model_dump())


@app.exception_handler(S3Error)
async def s3_exception_handler(request: Request, exception: S3Error):
    logger.error(f"S3Error: code={getattr(exception, 'code', None)}, message={getattr(exception, 'message', None)}")

    error_details = {
        "path": request.url.path,
        "error_message": getattr(exception, 'message', 'S3 operation failed')
    }

    # S3Error의 code가 None이거나 유효하지 않을 수 있음
    s3_code = getattr(exception, 'code', None)

    response = safe_create_response(
        status_code=safe_status_code(s3_code, 500),
        data=None,
        message=error_details,
    )
    return JSONResponse(status_code=500, content=response.model_dump())


@app.exception_handler(ApiException)
async def api_exception_handler(request: Request, exception: ApiException):
    logger.error(
        f"ApiException: status={getattr(exception, 'status', None)}, reason={getattr(exception, 'reason', None)}")

    error_details = {
        "type": "Kubernetes API Error",
        "reason": getattr(exception, 'reason', 'Unknown'),
    }

    # ApiException의 status가 None이거나 유효하지 않을 수 있음
    api_status = getattr(exception, 'status', None)

    response = safe_create_response(
        status_code=safe_status_code(api_status, 500),
        data=error_details,
        message=getattr(exception, 'body', 'Kubernetes API error occurred')
    )

    return JSONResponse(status_code=500, content=response.model_dump())


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exception: RequestValidationError):
    """RequestValidationError 처리 핸들러"""
    logger.error(f"RequestValidationError: {exception.errors()}")

    error_details = {
        "path": request.url.path,
        "method": request.method,
        "headers": dict(request.headers),
        "error_message": exception.errors(),
    }

    response = safe_create_response(
        status_code=400,
        data=None,
        message=error_details
    )
    return JSONResponse(status_code=400, content=response.model_dump())


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exception: SQLAlchemyError):
    logger.error(f"SQLAlchemyError: {str(exception)}")

    error_details = {
        "path": request.url.path,
        "details": str(exception)
    }

    response = safe_create_response(
        status_code=500,
        data=None,
        message=error_details
    )
    return JSONResponse(status_code=500, content=response.model_dump())


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exception: Exception):
    logger.error(f"Generic Exception: {type(exception).__name__}: {str(exception)}")

    response = safe_create_response(
        status_code=500,
        data=None,
        message=str(exception)
    )
    return JSONResponse(status_code=500, content=response.model_dump())