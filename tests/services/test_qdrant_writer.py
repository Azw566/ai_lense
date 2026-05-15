"""Tests for `upsert_products` and friends (P1-T5).

Runs against an in-memory `AsyncQdrantClient` after letting the init script
build the collection — exercises the same code path as production. The
contracts the writer commits to:

1. Each `ProductWithEmbedding` lands as one point with the minimal payload from
   arch §3.3 (`product_id`, `category`, `retailer`, `image_url` — nothing else).
2. Point IDs are deterministic via UUID5: re-running ingestion overwrites, not
   duplicates (the "Done when" check for P1-T5).
3. Rows missing the Postgres `product_id` or the canonical `category` are
   dropped with a warning rather than silently writing a half-broken point.
4. Batching of 100 doesn't lose anything — round-trip 250 points cleanly.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import numpy as np
import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.catalog.base import RawProduct
from app.catalog.embed_job import ProductWithEmbedding
from app.services.embedding import EMBED_DIM
from app.services.qdrant_writer import (
    COLLECTION_NAME,
    ProductPoint,
    point_id_for,
    points_from_embeddings,
    upsert_products,
)
from scripts.init_qdrant import ensure_collection


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncQdrantClient]:
    qdrant = AsyncQdrantClient(":memory:")
    await ensure_collection(qdrant)
    try:
        yield qdrant
    finally:
        await qdrant.close()


def _unit_vector(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(EMBED_DIM).astype(np.float32)
    return vec / np.linalg.norm(vec)


def _make_embedded(product_id: str, category: str = "couch") -> ProductWithEmbedding:
    raw = RawProduct(
        retailer="awin",
        retailer_product_id=product_id,
        title=f"Product {product_id}",
        source_category="Sofas",
        category=category,
        price=Decimal("199.00"),
        currency="EUR",
        image_url=f"https://img.example.com/{product_id}.jpg",
        product_url="https://shop.example.com",
        affiliate_url="https://awin1.com/x",
        brand=None,
        raw_payload={"id": product_id},
    )
    return ProductWithEmbedding(
        product=raw,
        embedding=_unit_vector(hash(product_id) % 2**31),
        cache_hit=False,
    )


# -- deterministic IDs ---------------------------------------------------------


def test_point_id_is_deterministic() -> None:
    a = point_id_for("awin", "ABC-1")
    b = point_id_for("awin", "ABC-1")
    c = point_id_for("awin", "ABC-2")
    assert a == b
    assert a != c
    # UUID5 by construction — version field encodes that.
    assert a.version == 5


def test_point_id_is_namespaced_by_retailer() -> None:
    # Two retailers can legitimately reuse the same product_id without
    # colliding in Qdrant.
    assert point_id_for("awin", "ID1") != point_id_for("ikea", "ID1")


# -- payload contract ----------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_writes_minimal_payload(client: AsyncQdrantClient) -> None:
    postgres_id = uuid.uuid4()
    embedded = [_make_embedded("P1")]
    points = points_from_embeddings(embedded, product_ids={("awin", "P1"): postgres_id})

    written = await upsert_products(client, points)
    assert written == 1

    record = (
        await client.retrieve(
            COLLECTION_NAME, ids=[str(point_id_for("awin", "P1"))], with_payload=True
        )
    )[0]
    # Exactly the four fields from arch §3.3 — nothing else.
    assert set(record.payload.keys()) == {"product_id", "category", "retailer", "image_url"}
    assert record.payload["product_id"] == str(postgres_id)
    assert record.payload["category"] == "couch"
    assert record.payload["retailer"] == "awin"
    assert record.payload["image_url"] == "https://img.example.com/P1.jpg"


# -- idempotent re-runs --------------------------------------------------------


@pytest.mark.asyncio
async def test_re_running_does_not_duplicate(client: AsyncQdrantClient) -> None:
    embedded = [_make_embedded(f"P{i}") for i in range(5)]
    product_ids = {("awin", row.product.retailer_product_id): uuid.uuid4() for row in embedded}
    points = points_from_embeddings(embedded, product_ids=product_ids)

    await upsert_products(client, points)
    first_count = (await client.count(COLLECTION_NAME)).count
    await upsert_products(client, points)
    second_count = (await client.count(COLLECTION_NAME)).count

    # Re-running adds 0 new points — the roadmap's explicit "Done when" check.
    assert first_count == 5
    assert second_count == 5


@pytest.mark.asyncio
async def test_re_run_with_updated_payload_overwrites(client: AsyncQdrantClient) -> None:
    # Same (retailer, retailer_product_id) but with a fresh category should
    # replace the old payload in-place, not stack two points.
    postgres_id = uuid.uuid4()
    first = _make_embedded("P1", category="couch")
    second = _make_embedded("P1", category="armchair")

    await upsert_products(
        client,
        points_from_embeddings([first], product_ids={("awin", "P1"): postgres_id}),
    )
    await upsert_products(
        client,
        points_from_embeddings([second], product_ids={("awin", "P1"): postgres_id}),
    )

    assert (await client.count(COLLECTION_NAME)).count == 1
    record = (
        await client.retrieve(
            COLLECTION_NAME, ids=[str(point_id_for("awin", "P1"))], with_payload=True
        )
    )[0]
    assert record.payload["category"] == "armchair"


# -- defensive skipping --------------------------------------------------------


def test_missing_postgres_id_is_skipped() -> None:
    embedded = [_make_embedded("P1"), _make_embedded("P2")]
    points = points_from_embeddings(embedded, product_ids={("awin", "P1"): uuid.uuid4()})
    # P2 has no Postgres id → silently dropped so we never write a Qdrant point
    # that joins to a missing row.
    assert {p.retailer_product_id for p in points} == {"P1"}


def test_missing_category_is_skipped() -> None:
    embedded = [_make_embedded("P1", category="")]
    points = points_from_embeddings(embedded, product_ids={("awin", "P1"): uuid.uuid4()})
    assert points == []


# -- batching ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batches_larger_than_batch_size_round_trip(client: AsyncQdrantClient) -> None:
    # 250 points crosses three batches of 100; verify nothing is lost in the
    # seam between batches.
    embedded = [_make_embedded(f"P{i}") for i in range(250)]
    product_ids = {("awin", row.product.retailer_product_id): uuid.uuid4() for row in embedded}
    points = points_from_embeddings(embedded, product_ids=product_ids)

    written = await upsert_products(client, points)
    assert written == 250
    assert (await client.count(COLLECTION_NAME)).count == 250


@pytest.mark.asyncio
async def test_empty_upsert_is_a_noop(client: AsyncQdrantClient) -> None:
    written = await upsert_products(client, [])
    assert written == 0
    assert (await client.count(COLLECTION_NAME)).count == 0


# -- ProductPoint conversion ---------------------------------------------------


def test_to_qdrant_preserves_vector_and_payload() -> None:
    postgres_id = uuid.uuid4()
    vector = _unit_vector(42)
    point = ProductPoint(
        product_id=postgres_id,
        retailer="awin",
        retailer_product_id="P1",
        category="couch",
        image_url="https://img.example.com/x.jpg",
        embedding=vector,
    )
    qpoint = point.to_qdrant()
    assert qpoint.id == str(point_id_for("awin", "P1"))
    assert len(qpoint.vector) == EMBED_DIM
    np.testing.assert_allclose(qpoint.vector, vector, atol=1e-6)
    assert qpoint.payload["product_id"] == str(postgres_id)
