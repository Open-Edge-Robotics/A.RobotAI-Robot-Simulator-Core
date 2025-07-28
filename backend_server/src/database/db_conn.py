from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.ext.declarative import declarative_base

from settings import settings

metadata = MetaData()

async_engine = create_async_engine(settings.DATABASE_URL, echo=True)
async_session = async_sessionmaker(async_engine, autoflush=False, autocommit=False)
Base = declarative_base()

async def get_db() -> AsyncSession:
    async with async_session() as db:
        yield db

async def init_db():
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def close_db():
    await async_engine.dispose()
