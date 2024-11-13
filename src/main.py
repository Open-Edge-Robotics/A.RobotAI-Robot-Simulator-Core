from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.database.connection import init_db, close_db
from src.routes.instance import instance_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"message": "Hello World"}

routers = [instance_router]
for router in routers:
    app.include_router(router)