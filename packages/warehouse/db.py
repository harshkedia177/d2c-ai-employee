from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.config import settings

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope(tenant_id: str):
    async with SessionLocal() as session:
        try:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": tenant_id},
            )
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
