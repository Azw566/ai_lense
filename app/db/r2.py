import aioboto3

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_session: aioboto3.Session | None = None


def get_r2_session() -> aioboto3.Session:
    """Return the global aioboto3 session, creating it lazily if needed."""
    global _session  # noqa: PLW0603

    if _session is None:
        _session = aioboto3.Session()
        logger.info("r2.session_created")
    return _session


async def ping_r2() -> bool:
    """Return True if the R2 bucket is accessible, False otherwise."""
    if not settings.r2_endpoint_url or not settings.r2_bucket_name:
        logger.error(
            "ping_r2.not_configured",
            message="R2 endpoint or bucket not set — check R2_ENDPOINT_URL and R2_BUCKET_NAME env vars",
        )
        return False

    try:
        session = get_r2_session()
        async with session.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
        ) as s3:
            await s3.head_bucket(Bucket=settings.r2_bucket_name)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("ping_r2.failed", error=str(exc))
        return False
