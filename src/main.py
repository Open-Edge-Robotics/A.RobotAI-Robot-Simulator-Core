from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError

from src.database.connection import init_db, close_db
from src.exception.exception_handler import http_exception_handler, validation_exception_handler
from src.routes import template, rosbag
from src.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # config.load_kube_config('/root/.kube/config') # TODO K8s 배포 시 주석 해제

    yield
    await close_db()

app = FastAPI(lifespan=lifespan)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)


routers = [template.router, rosbag.router]
for router in routers:
    app.include_router(router, prefix=settings.API_STR)

@app.get("/")
async def root():
    return {"message": "Hello World"}
