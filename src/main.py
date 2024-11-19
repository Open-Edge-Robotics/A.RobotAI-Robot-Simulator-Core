from contextlib import asynccontextmanager

from fastapi import FastAPI
from kubernetes import config

from src.database.connection import init_db, close_db
from src.routes import template, rosbag
from src.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # config.load_kube_config('/root/.kube/config')

    yield
    await close_db()

app = FastAPI(lifespan=lifespan)
routers = [template.router, rosbag.router]
for router in routers:
    app.include_router(router, prefix=settings.API_STR)

@app.get("/")
async def root():
    return {"message": "Hello World"}