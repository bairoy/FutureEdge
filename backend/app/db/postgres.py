# Import async database utilities from SQLAlchemy
from sqlalchemy.ext.asyncio import (
    create_async_engine,   # Creates the async database engine
    async_sessionmaker,    # Factory for creating async DB sessions
    AsyncSession           # Type/class representing an async DB session
)

# Import application settings
# Usually loaded from .env using Pydantic BaseSettings
from app.core.config import settings


# ============================================================
# DATABASE CONNECTION URL
# ============================================================

# We are constructing the PostgreSQL connection string dynamically
# using values stored inside settings.

# Final generated URL will look like:
#
# postgresql+asyncpg://username:password@host:port/database
#
# Example:
# postgresql+asyncpg://postgres:mypassword@localhost:5432/legal_ai
#
# Breakdown:
#
# postgresql
#   -> Database type
#
# +asyncpg
#   -> Async driver used for PostgreSQL
#      SQLAlchemy itself is not truly async.
#      It uses an async driver underneath.
#
# username:password
#   -> Database authentication credentials
#
# host:port
#   -> Where PostgreSQL server is running
#
# /database_name
#   -> Which database to connect to


DATABASE_URL = (
    f"postgresql+asyncpg://"
    f"{settings.POSTGRES_USER}:"
    f"{settings.POSTGRES_PASSWORD}@"
    f"{settings.POSTGRES_HOST}:"
    f"{settings.POSTGRES_PORT}/"
    f"{settings.POSTGRES_DB}"
)


# ============================================================
# CREATE DATABASE ENGINE
# ============================================================

# Engine is the CORE interface between SQLAlchemy and the database.
#
# Think of engine as:
#
# "The main connection manager that knows how to talk to PostgreSQL"
#
# It handles:
# - opening DB connections
# - connection pooling
# - SQL communication
# - async communication
# - transaction coordination
#
# IMPORTANT:
# Engine itself is NOT a single DB connection.
#
# It is a FACTORY + MANAGER for multiple connections.


engine = create_async_engine(

    # Connection URL we created above
    DATABASE_URL,

    # echo=True prints every SQL query in terminal
    #
    # Useful during development/debugging.
    #
    # Example logs:
    #
    # SELECT users.id, users.name FROM users
    #
    # In production usually set to False
    echo=True,
)


# ============================================================
# SESSION FACTORY
# ============================================================

# Session = temporary conversation with database
#
# VERY IMPORTANT CONCEPT:
#
# Engine manages CONNECTIONS.
# Session manages DATABASE OPERATIONS.
#
# Session is what you actually use inside API routes/services.
#
# Example:
#
# async with AsyncSessionLocal() as session:
#     result = await session.execute(query)
#
#
# async_sessionmaker creates a FACTORY that can generate
# new AsyncSession objects whenever needed.


AsyncSessionLocal = async_sessionmaker(

    # Bind this session factory to our engine
    #
    # Meaning:
    # "Whenever a session is created, use this engine"
    bind=engine,

    # Specify that sessions should be asynchronous
    #
    # Without this:
    # session would behave synchronously
    class_=AsyncSession,

    # IMPORTANT SQLAlchemy behavior
    #
    # expire_on_commit=False means:
    #
    # "After commit, keep object data available in memory"
    #
    # Example:
    #
    # user.name still accessible after commit
    #
    # If True:
    # SQLAlchemy expires object state after commit
    # and tries to re-fetch from DB again.
    #
    # In FastAPI async apps,
    # False is commonly preferred.
    expire_on_commit=False
)
