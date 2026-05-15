"""Upsert embedded products into Qdrant (roadmap P1-T5).

`upsert_products` is the only writer in the catalog pipeline. It takes the
output of `embed_products` (a `ProductWithEmbedding` per row) and writes one
Qdrant point per product with the minimal payload from arch §3.3:

    {product_id, category, retailer, image_url}

Point IDs are deterministic UUID5s derived from `(retailer, retailer_product_id)`
so re-running ingestion *overwrites* rather than duplicates — Qdrant treats the
point ID as the upsert key.

Postgres `product_id` is passed in by the caller because the writer never sees
the row that landed in `products` — that's the orchestrator's job (P1-T6). We
keep the writer narrow: vectors in, points out, nothing else.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

import numpy as np
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm

from app.catalog.embed_job import ProductWithEmbedding
from app.core.logging import get_logger

logger = get_logger(__name__)

COLLECTION_NAME = "products"
UPSERT_BATCH_SIZE = 100

# UUID5 namespace for catalog points. Chosen once and frozen — changing it would
# re-key every existing point and re-introduce the dup-on-re-run footgun.
_PRODUCT_NAMESPACE = uuid.UUID("3e3b2c1a-7d2f-4f6e-9a8c-1f0d2b3a4c5d")


def point_id_for(retailer: str, retailer_product_id: str) -> uuid.UUID:
    """Deterministic point ID. Same retailer+product always maps to the same UUID."""
    return uuid.uuid5(_PRODUCT_NAMESPACE, f"{retailer}:{retailer_product_id}")


@dataclass(slots=True)
class ProductPoint:
    """One product as it lands in Qdrant — a vector plus the join keys we'll
    need for filter+ANN and for hydrating results back from Postgres."""

    product_id: uuid.UUID  # Postgres UUID, used for hydration
    retailer: str
    retailer_product_id: str
    category: str
    image_url: str
    embedding: np.ndarray

    def to_qdrant(self) -> qm.PointStruct:
        # `tolist()` is typed as `float | list[float]` because numpy can't tell
        # 0-d from 1-d at type-check time — at runtime a (512,) array always
        # gives a `list[float]`, which is what `PointStruct.vector` wants.
        vector = cast(list[float], self.embedding.astype(np.float32).tolist())
        return qm.PointStruct(
            id=str(point_id_for(self.retailer, self.retailer_product_id)),
            vector=vector,
            payload={
                "product_id": str(self.product_id),
                "category": self.category,
                "retailer": self.retailer,
                "image_url": self.image_url,
            },
        )


def points_from_embeddings(
    embedded: Iterable[ProductWithEmbedding],
    *,
    product_ids: dict[tuple[str, str], uuid.UUID],
) -> list[ProductPoint]:
    """Pair `ProductWithEmbedding` rows with their Postgres `product_id`.

    `product_ids` is the orchestrator's lookup, keyed on
    `(retailer, retailer_product_id)`. Embedded rows whose product wasn't
    persisted to Postgres (the orchestrator should never let that happen, but
    defending in depth) are dropped with a warning so we never write a Qdrant
    point that points at a non-existent Postgres row.
    """
    points: list[ProductPoint] = []
    for row in embedded:
        key = (row.product.retailer, row.product.retailer_product_id)
        product_id = product_ids.get(key)
        if product_id is None:
            logger.warning(
                "qdrant.point.skipped",
                reason="missing_postgres_id",
                retailer=row.product.retailer,
                retailer_product_id=row.product.retailer_product_id,
            )
            continue
        if not row.product.category:
            # Qdrant payload `category` is keyword-indexed and queried as a
            # required filter — an unmapped category would never be returned,
            # so writing it is wasted space.
            logger.warning(
                "qdrant.point.skipped",
                reason="missing_category",
                retailer_product_id=row.product.retailer_product_id,
            )
            continue
        points.append(
            ProductPoint(
                product_id=product_id,
                retailer=row.product.retailer,
                retailer_product_id=row.product.retailer_product_id,
                category=row.product.category,
                image_url=row.product.image_url,
                embedding=row.embedding,
            )
        )
    return points


async def upsert_products(
    client: AsyncQdrantClient,
    points: list[ProductPoint],
    *,
    collection_name: str = COLLECTION_NAME,
    batch_size: int = UPSERT_BATCH_SIZE,
) -> int:
    """Upsert `points` into Qdrant in batches. Returns the number of points written.

    `wait=True` makes the call block until each batch is durable — slower per
    call, but ingestion correctness > ingestion speed (a crash between an
    "upserted" log line and actual durability would be invisible otherwise)."""
    if not points:
        return 0

    written = 0
    for start in range(0, len(points), batch_size):
        chunk = points[start : start + batch_size]
        await client.upsert(
            collection_name=collection_name,
            points=[point.to_qdrant() for point in chunk],
            wait=True,
        )
        written += len(chunk)

    logger.info(
        "qdrant.upsert.done",
        collection=collection_name,
        points=written,
    )
    return written
