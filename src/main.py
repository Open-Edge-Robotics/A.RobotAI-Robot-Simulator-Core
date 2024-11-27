from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from src.database.db_conn import init_db, close_db
from src.exception.exception_handler import http_exception_handler, validation_exception_handler
from src.routes import template, rosbag, instance, simulation
from src.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    from kubernetes import config
    config.load_kube_config('/root/.kube/config')  # TODO 로컬 실행 시 주석 처리

    yield
    await close_db()

app = FastAPI(lifespan=lifespan)

app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)

origins = [
    "http://localhost:3000"
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


routers = [template.router, rosbag.router, instance.router, simulation.router]
for router in routers:
    app.include_router(router, prefix=settings.API_STR)

@app.get("/")
async def root():
    return {"message": "Hello World"}
