"""Catalog embedding job (roadmap P1-T4).

Glue between `ProductSource` rows and the CLIP encoder. Three responsibilities:

1.  **Cache lookup** — every product's `image_url` is hashed (sha256) and looked
    up in `embedding_cache`. A cache hit means we already paid for the download
    + forward-pass and can skip both. Cost-control matters: at 1 M products a
    single re-run without cache would be hours of CPU and gigabytes of egress.
2.  **Encode misses** — products without a cached vector are downloaded and
    embedded in batches of 32 (`CLIPEmbedder.embed_pil_images` batches
    internally too). Each successfully embedded vector is written back to the
    cache. Failures (404, decode error, etc.) are dropped from the batch — one
    bad image never aborts the rest (arch §5.1).
3.  **Quarantine** — failed rows land in `unmapped_products` with a typed
    reason, so the run audit (P1-T6) can surface degradations early.

The public entry point is `embed_products(session, products) → list[ProductWithEmbedding]`.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import numpy as np
from PIL import Image
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.base import RawProduct
from app.core.logging import get_logger
from app.db.models import EmbeddingCache, UnmappedProduct
from app.services.embedding import (
    BATCH_SIZE,
    CLIPEmbedder,
    DownloadResult,
    default_embedder,
    download_image,
    embedding_from_bytes,
    embedding_to_bytes,
    sha256_of,
)

logger = get_logger(__name__)


@dataclass(slots=True)
class ProductWithEmbedding:
    """A product paired with its 512-d float32 L2-normalized image embedding."""

    product: RawProduct
    embedding: np.ndarray
    cache_hit: bool


async def embed_products(
    session: AsyncSession,
    products: list[RawProduct],
    *,
    embedder: CLIPEmbedder = default_embedder,
    http_client: httpx.Client | None = None,
) -> list[ProductWithEmbedding]:
    """Embed `products`, hitting `embedding_cache` first and quarantining failures.

    Returns only the successfully embedded products. Failed rows are written to
    `unmapped_products` inside this same session (caller commits)."""
    if not products:
        return []

    # Bucket products by their image-url hash so multiple products that share an
    # image (rare across retailers, common within a single retailer for variants)
    # share a single download + embed.
    hashes = [sha256_of(p.image_url) for p in products]
    cached = await _load_cache(session, hashes, embedder.model_version)

    results: list[ProductWithEmbedding] = []
    to_download: list[tuple[int, RawProduct, str]] = []
    for index, (product, image_hash) in enumerate(zip(products, hashes, strict=True)):
        if not product.image_url:
            await _quarantine(session, product, "missing_image_url")
            continue
        vector = cached.get(image_hash)
        if vector is not None:
            results.append(ProductWithEmbedding(product, vector, cache_hit=True))
        else:
            to_download.append((index, product, image_hash))

    if to_download:
        results.extend(await _download_and_embed(session, to_download, embedder, http_client))

    logger.info(
        "embed.batch.done",
        total=len(products),
        cache_hits=sum(1 for r in results if r.cache_hit),
        embedded=sum(1 for r in results if not r.cache_hit),
        failed=len(products) - len(results),
    )
    return results


async def _load_cache(
    session: AsyncSession, hashes: list[str], model_version: str
) -> dict[str, np.ndarray]:
    """Look up `hashes` in `embedding_cache`, scoped to the active model version.

    Rows produced by an older model version are ignored — mixing two encoders'
    vectors in the same Qdrant collection is the silent-corruption scenario."""
    if not hashes:
        return {}
    stmt = select(EmbeddingCache).where(
        EmbeddingCache.image_url_sha256.in_(set(hashes)),
        EmbeddingCache.model_version == model_version,
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {row.image_url_sha256: embedding_from_bytes(row.embedding) for row in rows}


async def _download_and_embed(
    session: AsyncSession,
    pending: list[tuple[int, RawProduct, str]],
    embedder: CLIPEmbedder,
    http_client: httpx.Client | None,
) -> list[ProductWithEmbedding]:
    """Download images, embed in batches of `BATCH_SIZE`, write back to cache."""
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=10.0, follow_redirects=True)
    try:
        results: list[ProductWithEmbedding] = []
        # Stream through `pending` in batch-sized chunks so peak memory stays
        # bounded by BATCH_SIZE images (~32 * 5 MB worst case) rather than the
        # whole shard.
        for start in range(0, len(pending), BATCH_SIZE):
            chunk = pending[start : start + BATCH_SIZE]
            usable: list[tuple[RawProduct, str, Image.Image]] = []
            for _, product, image_hash in chunk:
                outcome: DownloadResult = download_image(product.image_url, client=client)
                if outcome.image is None:
                    await _quarantine(session, product, outcome.reason or "download_failed")
                    continue
                usable.append((product, image_hash, outcome.image))

            if not usable:
                continue

            vectors = embedder.embed_pil_images([entry[2] for entry in usable])
            for (product, _image_hash, _), vector in zip(usable, vectors, strict=True):
                results.append(ProductWithEmbedding(product, vector, cache_hit=False))

            await _persist_cache(
                session,
                [
                    (image_hash, vec)
                    for (_, image_hash, _), vec in zip(usable, vectors, strict=True)
                ],
                embedder.model_version,
            )
        return results
    finally:
        if owns_client:
            client.close()


async def _persist_cache(
    session: AsyncSession,
    entries: list[tuple[str, np.ndarray]],
    model_version: str,
) -> None:
    """Upsert `entries` into `embedding_cache`. PG path uses `ON CONFLICT DO NOTHING`
    to be safe under concurrent runs; the SQLite (test) path falls back to a plain
    insert with row-by-row existence checks."""
    if not entries:
        return
    rows = [
        {
            "image_url_sha256": image_hash,
            "embedding": embedding_to_bytes(vector),
            "model_version": model_version,
        }
        for image_hash, vector in entries
    ]
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect == "postgresql":
        stmt = pg_insert(EmbeddingCache).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["image_url_sha256"])
        await session.execute(stmt)
        return

    # SQLite (tests): no ON CONFLICT here to keep the path dialect-agnostic.
    existing = (
        (
            await session.execute(
                select(EmbeddingCache.image_url_sha256).where(
                    EmbeddingCache.image_url_sha256.in_([row["image_url_sha256"] for row in rows])
                )
            )
        )
        .scalars()
        .all()
    )
    new_rows = [row for row in rows if row["image_url_sha256"] not in set(existing)]
    if new_rows:
        session.add_all(EmbeddingCache(**row) for row in new_rows)


async def _quarantine(session: AsyncSession, product: RawProduct, reason: str) -> None:
    """Write a row to `unmapped_products` with the failure reason and original payload."""
    logger.warning(
        "embed.product.failed",
        retailer=product.retailer,
        retailer_product_id=product.retailer_product_id,
        reason=reason,
    )
    session.add(
        UnmappedProduct(
            retailer=product.retailer,
            retailer_product_id=product.retailer_product_id,
            source_category=product.source_category,
            reason=reason,
            raw_payload=product.raw_payload or {},
        )
    )
