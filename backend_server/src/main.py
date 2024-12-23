from contextlib import asynccontextmanager

from fastapi.middleware.cors import CORSMiddleware

from .database.db_conn import init_db, close_db
from .exception.exception_handler import *
from .routes import template, instance, simulation
from .settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    yield
    await close_db()


app = FastAPI(lifespan=lifespan)

app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(S3Error, s3_exception_handler)
app.add_exception_handler(ApiException, api_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)

origins = [
    "http://localhost:3000",
    "http://192.168.160.134:3001",
    "http://192.168.160.129:3001"
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

routers = [template.router, instance.router, simulation.router]
for router in routers:
    app.include_router(router, prefix=settings.API_STR)


@app.get("/")
async def root():
    return {"message": "Hello World"}
