import os

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.ext.declarative import declarative_base

db_url = os.getenv("DATABASE_URL")
async_engine = create_async_engine(db_url, echo=True)
async_session = async_sessionmaker(async_engine, autoflush=False, autocommit=False)

Base = declarative_base()
metadata = MetaData()

async def get_db() -> AsyncSession:
    await init_db()

    db = async_session()
    try:
        yield db
    finally:
        await db.close()

async def init_db():
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def close_db():
    await async_engine.dispose()