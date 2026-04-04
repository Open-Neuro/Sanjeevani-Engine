"""
app/database/mongo_client.py
─────────────────────────────────────────────────────────────────────────────
MongoDB connection manager for SanjeevaniRxAI.

Design
──────
• Singleton MongoClient – one connection pool per process lifetime.
• ``get_client()``  → returns the shared MongoClient (connects lazily).
• ``get_db()``      → returns the target Database object (from config).
• ``close_client()``→ clean shutdown (called from FastAPI lifespan).
• ``health_check()``→ lightweight ping to verify connectivity.

Usage
─────
    from app.database.mongo_client import get_db, health_check

    db = get_db()
    result = db["consumer_orders"].find_one({"Patient Name": "Alice"})

    # In FastAPI startup / shutdown:
    from app.database.mongo_client import get_client, close_client
"""

from __future__ import annotations

import threading
from typing import Optional

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import (
    ConfigurationError,
    ConnectionFailure,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Thread-safe singleton state
# ──────────────────────────────────────────────────────────────────────────────

_lock: threading.Lock = threading.Lock()
_client: Optional[MongoClient] = None  # type: ignore[type-arg]


# ──────────────────────────────────────────────────────────────────────────────
# Internal factory (called once)
# ──────────────────────────────────────────────────────────────────────────────


def _create_client() -> MongoClient:  # type: ignore[type-arg]
    """
    Instantiate and return a configured MongoClient.

    Connection-pool and timeout settings are tuned for a typical
    multi-threaded FastAPI/uvicorn deployment.

    Raises
    ------
    ConfigurationError
        If the URI is malformed.
    ConnectionFailure
        If the initial ping fails.
    """
    try:
        logger.info(
            "Initialising MongoDB client",
            extra={"uri_prefix": settings.MONGO_URI[:30] + "…"},
        )

        client: MongoClient = MongoClient(  # type: ignore[type-arg]
            settings.MONGO_URI,
            # Timeouts (milliseconds)
            serverSelectionTimeoutMS=15_000,  # increased for flakey DNS
            connectTimeoutMS=15_000,
            socketTimeoutMS=30_000,
            # Connection pool
            maxPoolSize=50,
            minPoolSize=5,
            maxIdleTimeMS=60_000,
            # Reliability
            retryWrites=True,
            retryReads=True,
            # App identification (shows in MongoDB Atlas performance advisor)
            appName="SanjeevaniRxAI",
        )

        # Verify connectivity immediately (raises on failure)
        client.admin.command("ping")

        logger.info(
            "MongoDB connection established",
            extra={"db": settings.DB_NAME},
        )
        return client

    except ConfigurationError as exc:
        logger.critical(
            f"MongoDB URI is malformed: {exc}",
            extra={"error": str(exc)},
        )
        raise

    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        logger.critical(
            "Cannot connect to MongoDB – is the server running?",
            extra={"uri_prefix": settings.MONGO_URI[:30] + "…", "error": str(exc)},
        )
        raise


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────


def get_client() -> MongoClient:  # type: ignore[type-arg]
    """
    Return the process-wide MongoClient singleton (thread-safe).

    The client is created lazily on the first call and reused for all
    subsequent calls within the same process.
    """
    global _client
    if _client is None:
        with _lock:
            # Double-checked locking
            if _client is None:
                _client = _create_client()
    return _client


def get_db(db_name: Optional[str] = None) -> Database:  # type: ignore[type-arg]
    """
    Return a MongoDB :class:`~pymongo.database.Database` instance.

    Parameters
    ----------
    db_name:
        Database name to use.  Defaults to ``settings.DB_NAME``
        (``pharmacy_management``).

    Returns
    -------
    pymongo.database.Database
        The requested database object.
    """
    name = db_name or settings.DB_NAME
    return get_client()[name]


def close_client() -> None:
    """
    Close the MongoDB connection pool.

    Call this once during application shutdown (FastAPI lifespan ``finally``
    block) to ensure all sockets are properly released.
    """
    global _client
    with _lock:
        if _client is not None:
            logger.info("Closing MongoDB connection pool…")
            _client.close()
            _client = None
            logger.info("MongoDB connection closed.")


# ──────────────────────────────────────────────────────────────────────────────
# Health check
# ──────────────────────────────────────────────────────────────────────────────


def health_check() -> dict[str, object]:
    """
    Ping MongoDB and return a status dictionary.

    Returns
    -------
    dict
        ``{"status": "ok", "latency_ms": float}``  on success, or
        ``{"status": "error", "detail": str}``      on failure.

    This is intentionally non-raising so it can be called safely from a
    ``/health`` endpoint without crashing the service.
    """
    import time

    try:
        client = get_client()
        start = time.perf_counter()
        client.admin.command("ping")
        elapsed_ms = round((time.perf_counter() - start) * 1_000, 2)

        logger.debug("MongoDB health check passed", extra={"latency_ms": elapsed_ms})
        return {
            "status": "ok",
            "latency_ms": elapsed_ms,
            "database": settings.DB_NAME,
        }

    except (ConnectionFailure, ServerSelectionTimeoutError, OperationFailure) as exc:
        logger.error(
            "MongoDB health check failed",
            extra={"error": str(exc)},
        )
        return {
            "status": "error",
            "detail": str(exc),
        }
