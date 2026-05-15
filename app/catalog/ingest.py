"""Catalog ingestion orchestrator (roadmap P1-T6).

Wires `ProductSource` → Postgres `products` → CLIP embedding → Qdrant points
into one batched pipeline, with a single `indexing_runs` row capturing what
happened (counts, error sample, status) — the first thing to query when a feed
silently degrades from 95 % success to 60 %.

The orchestrator owns the *flow*, not any single component:

* It pulls `RawProduct`s from the source in batches so peak memory stays bounded
  by `batch_size`, not the size of the feed (Awin feeds run to ~10 k–500 k rows).
* Mapped rows are upserted into Postgres; the returned `product_id`s feed the
  Qdrant write so points always point at a row that actually exists.
* Unmapped rows (no canonical category) skip both encoder and Qdrant and land
  in `unmapped_products` for triage — ingestion never blocks on bad rows
  (arch §5.1).
* The whole run is wrapped in a try/finally so partial failures still leave a
  finalized `indexing_runs` row (status=`error`, with a `notes` snippet)
  rather than a forever-`running` ghost.
"""

from __future__ import annotations

import traceback
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone  # noqa: UP017
from itertools import islice
from typing import TypeVar

import httpx
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.base import ProductSource, RawProduct
from app.catalog.embed_job import ProductWithEmbedding, embed_products
from app.core.logging import get_logger
from app.db.models import IndexingRun, Product, UnmappedProduct
from app.services.embedding import CLIPEmbedder, default_embedder
from app.services.qdrant_writer import points_from_embeddings, upsert_products

logger = get_logger(__name__)

DEFAULT_BATCH_SIZE = 64
# Cap on how many distinct error strings we keep in `indexing_runs.notes`.
# Notes is for "what kind of failures did we see" — not a full error log.
MAX_ERROR_SAMPLE = 5

# PEP 695 type-parameter syntax isn't available until Python 3.12; until the
# local venv is rebuilt on 3.12 (see ISSUES.md §4) we use the classic TypeVar.
T = TypeVar("T")


@dataclass(slots=True)
class IngestStats:
    """Mutable counters and a small error sample, written to `indexing_runs`."""

    products_in: int = 0  # rows yielded by the source
    products_out: int = 0  # points written to Qdrant
    unmapped: int = 0  # rows quarantined for unknown category
    embed_failures: int = 0  # rows quarantined for download/decode failure
    errors: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        if message and message not in self.errors and len(self.errors) < MAX_ERROR_SAMPLE:
            self.errors.append(message)


def _batched(iterable: Iterable[T], size: int) -> Iterator[list[T]]:  # noqa: UP047
    """Yield successive lists of length `size` (last batch may be shorter)."""
    iterator = iter(iterable)
    while batch := list(islice(iterator, size)):
        yield batch


async def _start_run(session: AsyncSession, source: str) -> IndexingRun:
    run = IndexingRun(source=source, status="running")
    session.add(run)
    await session.flush()
    return run


async def _finalize_run(
    session: AsyncSession,
    run: IndexingRun,
    stats: IngestStats,
    *,
    status: str,
    note: str | None = None,
) -> None:
    """Close out the audit row with counts and an optional terminal note.

    Always runs in `finally`, even when the pipeline raised — a half-finished
    run with `status='running'` is the failure mode this is designed to avoid.
    """
    run.finished_at = datetime.now(timezone.utc)  # noqa: UP017
    run.status = status
    run.products_in = stats.products_in
    run.products_out = stats.products_out
    run.errors_count = stats.unmapped + stats.embed_failures
    parts = list(stats.errors)
    if note:
        parts.append(note)
    if parts:
        run.notes = " | ".join(parts)
    await session.flush()


async def _persist_mapped_products(
    session: AsyncSession, mapped: list[RawProduct]
) -> dict[tuple[str, str], uuid.UUID]:
    """Upsert mapped rows into `products` and return `(retailer, retailer_product_id) → id`.

    Postgres path uses `ON CONFLICT (retailer, retailer_product_id) DO UPDATE`
    so re-runs refresh price/title/category in place. SQLite (used in tests)
    falls back to a row-by-row select+update because it lacks the same syntax."""
    if not mapped:
        return {}

    dialect = session.bind.dialect.name if session.bind is not None else ""
    rows = [
        {
            "id": uuid.uuid4(),
            "retailer": product.retailer,
            "retailer_product_id": product.retailer_product_id,
            "title": product.title,
            "brand": product.brand,
            # `mapped` only contains rows where category is not None, but the
            # type system doesn't know that — fall back to '' rather than None
            # to satisfy the NOT NULL constraint defensively.
            "category": product.category or "",
            "price_eur": float(product.price) if product.price is not None else None,
            "currency": product.currency or "EUR",
            "image_url": product.image_url,
            "product_url": product.product_url,
            "raw_payload": product.raw_payload or {},
        }
        for product in mapped
    ]

    if dialect == "postgresql":
        stmt = pg_insert(Product).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_products_retailer_product",
            set_={
                "title": stmt.excluded.title,
                "brand": stmt.excluded.brand,
                "category": stmt.excluded.category,
                "price_eur": stmt.excluded.price_eur,
                "currency": stmt.excluded.currency,
                "image_url": stmt.excluded.image_url,
                "product_url": stmt.excluded.product_url,
                "raw_payload": stmt.excluded.raw_payload,
            },
        )
        await session.execute(stmt)
    else:
        # SQLite test path: existence-check then add-or-update one row at a time.
        for row in rows:
            existing = await session.execute(
                select(Product).where(
                    Product.retailer == row["retailer"],
                    Product.retailer_product_id == row["retailer_product_id"],
                )
            )
            current = existing.scalar_one_or_none()
            if current is None:
                session.add(Product(**row))
            else:
                for field_name in (
                    "title",
                    "brand",
                    "category",
                    "price_eur",
                    "currency",
                    "image_url",
                    "product_url",
                    "raw_payload",
                ):
                    setattr(current, field_name, row[field_name])
    await session.flush()

    # Re-read the canonical ids — the rows we just inserted may have collided
    # with existing rows, in which case the id we generated above isn't the
    # one Postgres kept. One round-trip select keyed on the (retailer, id) pair.
    keys = [(row["retailer"], row["retailer_product_id"]) for row in rows]
    if not keys:
        return {}
    retailer = mapped[0].retailer
    product_ids = (
        await session.execute(
            select(Product.retailer, Product.retailer_product_id, Product.id).where(
                Product.retailer == retailer,
                Product.retailer_product_id.in_([row["retailer_product_id"] for row in rows]),
            )
        )
    ).all()
    return {(row.retailer, row.retailer_product_id): row.id for row in product_ids}


async def _quarantine_unmapped(session: AsyncSession, rows: list[RawProduct]) -> None:
    """Drop unmapped rows into `unmapped_products` so they can be triaged later."""
    if not rows:
        return
    for product in rows:
        session.add(
            UnmappedProduct(
                retailer=product.retailer,
                retailer_product_id=product.retailer_product_id,
                source_category=product.source_category,
                reason="unmapped_category",
                raw_payload=product.raw_payload or {},
            )
        )
    await session.flush()


async def _process_batch(
    session: AsyncSession,
    qdrant: AsyncQdrantClient,
    batch: list[RawProduct],
    stats: IngestStats,
    *,
    embedder: CLIPEmbedder,
    http_client: httpx.Client | None,
) -> None:
    """Persist → embed → upsert one batch. Unmapped rows are split off here so
    they never reach the encoder."""
    mapped: list[RawProduct] = []
    unmapped: list[RawProduct] = []
    for product in batch:
        (mapped if product.is_mapped else unmapped).append(product)

    stats.unmapped += len(unmapped)
    await _quarantine_unmapped(session, unmapped)

    if not mapped:
        return

    product_ids = await _persist_mapped_products(session, mapped)
    embedded: list[ProductWithEmbedding] = await embed_products(
        session, mapped, embedder=embedder, http_client=http_client
    )
    stats.embed_failures += len(mapped) - len(embedded)

    points = points_from_embeddings(embedded, product_ids=product_ids)
    written = await upsert_products(qdrant, points)
    stats.products_out += written


async def run_ingestion(
    session: AsyncSession,
    qdrant: AsyncQdrantClient,
    source: ProductSource,
    *,
    since: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    limit: int | None = None,
    embedder: CLIPEmbedder = default_embedder,
    http_client: httpx.Client | None = None,
) -> IngestStats:
    """Drive a full ingestion run and return the stats written to `indexing_runs`."""
    run = await _start_run(session, source.retailer)
    stats = IngestStats()

    def iter_source() -> Iterator[RawProduct]:
        for index, product in enumerate(source.list_products(since=since)):
            if limit is not None and index >= limit:
                return
            yield product

    try:
        for batch in _batched(iter_source(), batch_size):
            stats.products_in += len(batch)
            try:
                await _process_batch(
                    session,
                    qdrant,
                    batch,
                    stats,
                    embedder=embedder,
                    http_client=http_client,
                )
                # Commit after each batch: a crash mid-feed leaves earlier
                # batches durable rather than wasting all the encode work.
                await session.commit()
            except Exception as exc:  # noqa: BLE001
                stats.add_error(f"{type(exc).__name__}: {exc}")
                logger.exception("ingest.batch.failed", retailer=source.retailer)
                await session.rollback()

        await _finalize_run(session, run, stats, status="success")
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        # A failure here is structural (DB down, Qdrant down) — record it and
        # surface so the CLI exits non-zero.
        stats.add_error(f"{type(exc).__name__}: {exc}")
        await session.rollback()
        # Re-open a session-level transaction so finalize can write.
        await _finalize_run(
            session,
            run,
            stats,
            status="error",
            note=traceback.format_exception_only(type(exc), exc)[-1].strip(),
        )
        await session.commit()
        raise
    return stats
