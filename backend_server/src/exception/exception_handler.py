from fastapi import HTTPException as StarletteHTTPException
from fastapi import Request, FastAPI
from fastapi.exceptions import RequestValidationError
from kubernetes.client import ApiException
from minio import S3Error
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from backend_server.src.schemas.format import GlobalResponseModel

app = FastAPI()


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exception: StarletteHTTPException):
    """HTTPException 처리 핸들러"""

    response = GlobalResponseModel(
        status_code=exception.status_code,
        data=None,
        message=str(exception.detail),
    )
    return JSONResponse(status_code=exception.status_code, content=response.model_dump())


@app.exception_handler(S3Error)
async def s3_exception_handler(request: Request, exception: S3Error):
    error_details = {
        "path": request.url.path,
        "error_message": exception.message
    }

    response = GlobalResponseModel(
        status_code=exception.code,
        data=None,
        message=error_details,
    )
    return JSONResponse(status_code=500, content=response.model_dump())


# K8s 에러
@app.exception_handler(ApiException)
async def api_exception_handler(request: Request, exception: ApiException):
    error_details = {
        "type": "Kubernetes API Error",
        "reason": exception.reason,
    }

    response = GlobalResponseModel(
        status_code=exception.status,
        data=error_details,
        message=exception.body
    )

    return JSONResponse(status_code=500, content=response.model_dump())


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exception: RequestValidationError):
    """RequestValidationError 처리 핸들러"""
    error_details = {
        "path": request.url.path,
        "method": request.method,
        "headers": dict(request.headers),
        "error_message": exception.errors(),
    }

    response = GlobalResponseModel(
        status_code=400,
        data=None,
        message=error_details
    )
    return JSONResponse(status_code=400, content=response.model_dump())


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exception: SQLAlchemyError):
    error_details = {
        "path": request.url.path,
        "details": str(exception)
    }

    response = GlobalResponseModel(
        status_code=500,
        data=None,
        message=error_details
    )
    return JSONResponse(status_code=500, content=response.model_dump())


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exception: Exception):
    response = GlobalResponseModel(
        status_code=500,
        data=None,
        message=str(exception)
    )
    return JSONResponse(status_code=500, content=response.model_dump())
