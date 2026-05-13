import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_redis_client: aioredis.Redis | None = None  # type: ignore[type-arg]


def get_redis() -> aioredis.Redis:  # type: ignore[type-arg]
    """Return the global Redis client. Raises RuntimeError if not yet initialised."""
    if _redis_client is None:
        raise RuntimeError("Redis client has not been initialised. Call init_redis() first.")
    return _redis_client


async def init_redis() -> aioredis.Redis:  # type: ignore[type-arg]
    """Create a Redis connection pool and store it as the global client."""
    global _redis_client  # noqa: PLW0603

    pool = aioredis.ConnectionPool.from_url(
        settings.redis_url,
        max_connections=10,
        decode_responses=False,
    )
    _redis_client = aioredis.Redis(connection_pool=pool)
    logger.info("redis.initialised", url=settings.redis_url)
    return _redis_client


async def close_redis() -> None:
    """Close the global Redis client and release all connections."""
    global _redis_client  # noqa: PLW0603

    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("redis.closed")


async def ping_redis() -> bool:
    """Return True if Redis is reachable, False otherwise."""
    try:
        client = get_redis()
        result = await client.ping()
        return bool(result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ping_redis.failed", error=str(exc))
        return False
