"""

==================
PostgreSQL connection using SQLAlchemy async.

TWO THINGS ARE CREATED HERE:
------------------------------
1. engine            — the connection pool manager (one per app)
2. AsyncSessionLocal — a factory that creates DB sessions

HOW TO USE A SESSION IN ANY ASYNC FUNCTION:
--------------------------------------------
    from app.db.postgres import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        result = await session.execute(query)
        await session.commit()

WHY ASYNCPG?
-------------
asyncpg is a pure-Python async PostgreSQL driver.
It never blocks the event loop — important for a trading
system where every millisecond matters.
"""

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)

from app.core.config import settings


# ============================================================
# CONNECTION URL
# ============================================================

# Format: postgresql+asyncpg://user:password@host:port/database
# "+asyncpg" tells SQLAlchemy to use the async driver.

DATABASE_URL = (
    f"postgresql+asyncpg://"
    f"{settings.POSTGRES_USER}:"
    f"{settings.POSTGRES_PASSWORD}@"
    f"{settings.POSTGRES_HOST}:"
    f"{settings.POSTGRES_PORT}/"
    f"{settings.POSTGRES_DB}"
)


# ============================================================
# ENGINE  (one per application lifetime)
# ============================================================

# pool_pre_ping=True : before reusing a pooled connection,
#   ping it to confirm it is still alive.
#   Prevents "server closed connection" errors after idle.
#
# pool_size=10  : keep 10 connections open at all times.
# max_overflow=20 : allow 20 extra connections under heavy load.

engine = create_async_engine(
    DATABASE_URL,
    echo=False,           # set True to print every SQL query
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)


# ============================================================
# SESSION FACTORY
# ============================================================

# Call AsyncSessionLocal() to create a new session each time.
# A session = one unit of work with the DB.
#
# expire_on_commit=False :
#   After commit(), ORM objects stay readable in Python memory.
#   Without this, SQLAlchemy tries to reload them from DB
#   immediately after commit — which fails in async code.

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)