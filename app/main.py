from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog.contextvars
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.api.middleware import RequestIDMiddleware
from app.db.postgres import engine, ping_postgres
from app.db.redis import init_redis, close_redis, ping_redis
from app.db.qdrant import init_qdrant, close_qdrant, ping_qdrant
from app.db.r2 import ping_r2

# Configure structured logging before anything else runs.
configure_logging()

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("startup", service="visual-deco-search", message="Application starting up")

    # --- Initialise connections ---
    logger.info("startup.redis", message="Initialising Redis connection pool")
    await init_redis()

    logger.info("startup.qdrant", message="Initialising Qdrant client")
    await init_qdrant()

    # Postgres engine is created lazily; no explicit init needed.

    # --- Health-check each backend at startup ---
    ping_results: dict[str, bool] = {
        "pg": await ping_postgres(),
        "redis": await ping_redis(),
        "qdrant": await ping_qdrant(),
        "r2": await ping_r2(),
    }

    for service, healthy in ping_results.items():
        if healthy:
            logger.info("startup.ping_ok", service=service)
        else:
            logger.warning("startup.ping_failed", service=service)

    if not all(ping_results.values()):
        if settings.app_env != "development":
            failed = [svc for svc, ok in ping_results.items() if not ok]
            raise RuntimeError(f"Startup health-check failed for: {failed}")
        else:
            logger.warning(
                "startup.degraded",
                message="One or more backends unreachable — continuing in development mode",
                failed=[svc for svc, ok in ping_results.items() if not ok],
            )

    logger.info("startup.done", service="visual-deco-search", message="Application ready")

    yield

    # --- Shutdown ---
    logger.info("shutdown", service="visual-deco-search", message="Application shutting down")

    await close_redis()
    await close_qdrant()
    await engine.dispose()

    logger.info("shutdown.done", service="visual-deco-search", message="All connections closed")


app = FastAPI(title="Visual Deco Search", lifespan=lifespan)

app.add_middleware(RequestIDMiddleware)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    checks: dict[str, bool] = {
        "pg": await ping_postgres(),
        "redis": await ping_redis(),
        "qdrant": await ping_qdrant(),
        "r2": await ping_r2(),
    }
    status = "ok" if all(checks.values()) else "degraded"
    request_id: str = structlog.contextvars.get_contextvars().get("request_id", "")
    status_code = 200 if status == "ok" else 503
    return JSONResponse(
        content={"status": status, "checks": checks, "request_id": request_id},
        status_code=status_code,
    )


@app.get("/livez")
async def livez() -> dict[str, str]:
    return {"status": "ok"}
