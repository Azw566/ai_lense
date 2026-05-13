from qdrant_client import AsyncQdrantClient

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_qdrant_client: AsyncQdrantClient | None = None


def get_qdrant() -> AsyncQdrantClient:
    """Return the global Qdrant client. Raises RuntimeError if not yet initialised."""
    if _qdrant_client is None:
        raise RuntimeError("Qdrant client has not been initialised. Call init_qdrant() first.")
    return _qdrant_client


async def init_qdrant() -> AsyncQdrantClient:
    """Create an async Qdrant client and store it as the global singleton."""
    global _qdrant_client  # noqa: PLW0603

    kwargs: dict[str, str] = {"url": settings.qdrant_url}
    if settings.qdrant_api_key:
        kwargs["api_key"] = settings.qdrant_api_key

    _qdrant_client = AsyncQdrantClient(**kwargs)
    logger.info("qdrant.initialised", url=settings.qdrant_url)
    return _qdrant_client


async def close_qdrant() -> None:
    """Close the global Qdrant client."""
    global _qdrant_client  # noqa: PLW0603

    if _qdrant_client is not None:
        await _qdrant_client.close()
        _qdrant_client = None
        logger.info("qdrant.closed")


async def ping_qdrant() -> bool:
    """Return True if Qdrant is reachable, False otherwise."""
    try:
        client = get_qdrant()
        await client.get_collections()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("ping_qdrant.failed", error=str(exc))
        return False
