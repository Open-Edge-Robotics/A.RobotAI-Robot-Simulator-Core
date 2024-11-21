from fastapi import HTTPException as StarletteHTTPException
from fastapi import Request, FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

from src.schemas.format import GlobalResponseModel

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

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exception: RequestValidationError):
    """RequestValidationError 처리 핸들러"""
    # TODO request 객체 활용 로깅하려면 사용
    error_details = {
        "path": request.url.path,
        "method": request.method,
        "headers": dict(request.headers),
        "error_message": dict(exception.errors()),
    }

    response = GlobalResponseModel(
        status_code=400,
        data=None,
        message=error_details
    )
    return JSONResponse(status_code=400, content=response.model_dump())

