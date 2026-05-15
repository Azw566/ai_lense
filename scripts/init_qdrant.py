"""Create the `products` Qdrant collection idempotently (roadmap P1-T5).

The collection is the search index — Postgres is the source of truth, Qdrant
holds the vectors plus the minimum payload needed to (a) filter by category /
retailer pre-ANN and (b) join back to Postgres for hydration (arch §3.3).

Idempotent on every dimension: if the collection exists with the right shape
the script no-ops; if a payload index is already present, `create_payload_index`
is a no-op too. Safe to run on every deploy.

Usage:
    python scripts/init_qdrant.py [--recreate]

`--recreate` drops and re-creates the collection — destructive, only use when
the vector dimension or distance metric changes (i.e. a new encoder).
"""

from __future__ import annotations

import argparse
import asyncio

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.services.embedding import EMBED_DIM

logger = get_logger(__name__)

COLLECTION_NAME = "products"

# HNSW defaults from the architecture doc. m=16 is the standard build for
# cosine-normalized embeddings; ef_construct=128 trades a slower one-time build
# for better recall — fine until we cross ~1 M vectors and want to tune up.
HNSW_M = 16
HNSW_EF_CONSTRUCT = 128

# Payload fields we index. WITHOUT these, Qdrant filter+ANN falls back to a
# brute filter scan — order-of-magnitude slower at scale (arch §1).
INDEXED_PAYLOAD_FIELDS: tuple[tuple[str, qm.PayloadSchemaType], ...] = (
    ("category", qm.PayloadSchemaType.KEYWORD),
    ("retailer", qm.PayloadSchemaType.KEYWORD),
)


def _client() -> AsyncQdrantClient:
    kwargs: dict[str, str | None] = {"url": settings.qdrant_url}
    if settings.qdrant_api_key:
        kwargs["api_key"] = settings.qdrant_api_key
    return AsyncQdrantClient(**kwargs)  # type: ignore[arg-type]


async def ensure_collection(client: AsyncQdrantClient, *, recreate: bool = False) -> None:
    """Create the products collection if absent. With `recreate=True`, drop first."""
    if recreate and await client.collection_exists(COLLECTION_NAME):
        logger.warning("qdrant.collection.recreating", name=COLLECTION_NAME)
        await client.delete_collection(COLLECTION_NAME)

    if await client.collection_exists(COLLECTION_NAME):
        logger.info("qdrant.collection.exists", name=COLLECTION_NAME)
    else:
        await client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=qm.VectorParams(
                size=EMBED_DIM,
                distance=qm.Distance.COSINE,
            ),
            hnsw_config=qm.HnswConfigDiff(
                m=HNSW_M,
                ef_construct=HNSW_EF_CONSTRUCT,
            ),
        )
        logger.info(
            "qdrant.collection.created",
            name=COLLECTION_NAME,
            dim=EMBED_DIM,
            distance="cosine",
        )

    for field_name, schema in INDEXED_PAYLOAD_FIELDS:
        # `create_payload_index` is idempotent server-side — re-running on an
        # existing index is a no-op, so no exists-check needed.
        await client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name=field_name,
            field_schema=schema,
        )
        logger.info(
            "qdrant.payload_index.ensured",
            field=field_name,
            schema=schema.value,
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Drop and re-create the collection (destructive)",
    )
    args = parser.parse_args()

    configure_logging()
    client = _client()
    try:
        await ensure_collection(client, recreate=args.recreate)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
