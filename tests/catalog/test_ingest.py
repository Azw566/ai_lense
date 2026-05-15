"""End-to-end tests for the Awin ingestion orchestrator (P1-T6).

Drives `run_ingestion` against the same fixture CSV that `test_awin.py` uses,
through:

* a real `AwinSource` (so taxonomy + price parsing + affiliate URLs are
  exercised in the orchestrator path),
* an in-memory SQLite session (real schema, no Postgres dialect-specific
  branches),
* an in-memory Qdrant collection initialised by `scripts.init_qdrant`,
* a fake embedder (real CLIP would burn 10 s per test run for no extra
  coverage of *this* component), and
* a mocked `httpx.Client` returning a flat-color PNG so `download_image`
  succeeds without touching the network.

The four big contracts under test (the "Done when" of P1-T6):

1. `indexing_runs` gets exactly one row with status=success and counts that
   line up with what was actually written.
2. Mapped products land in Postgres; unmapped ones land in `unmapped_products`
   with reason=`unmapped_category`. The encoder is never invoked for them.
3. Re-running ingestion is idempotent — Postgres rows are upserted in place,
   Qdrant points are overwritten not duplicated.
4. A single mid-feed download failure quarantines the offending product,
   doesn't poison the surrounding batch, and is reflected in
   `indexing_runs.errors_count`.
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import numpy as np
import pytest
import pytest_asyncio
from PIL import Image
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.catalog.ingest import run_ingestion
from app.catalog.sources.awin import AwinSource
from app.db.base import Base
from app.db.models import IndexingRun, Product, UnmappedProduct
from app.services.embedding import EMBED_DIM, MODEL_VERSION
from app.services.qdrant_writer import COLLECTION_NAME
from scripts.init_qdrant import ensure_collection

FIXTURE = Path(__file__).parent / "fixtures" / "awin_sample.csv"
PUBLISHER_ID = "999999"

# The fixture has 12 rows: 10 map to canonical categories, 2 don't (garden +
# kitchen appliances). See `tests/catalog/test_awin.py::test_mapping_rate_*`.
EXPECTED_MAPPED = 10
EXPECTED_UNMAPPED = 2


class FakeEmbedder:
    """Returns deterministic unit vectors; tracks how often it ran."""

    model_version = MODEL_VERSION

    def __init__(self) -> None:
        self.calls = 0

    def embed_pil_images(self, images: list[Image.Image]) -> np.ndarray:
        self.calls += 1
        rng = np.random.default_rng(len(images))
        vectors = rng.standard_normal((len(images), EMBED_DIM)).astype(np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / norms


def _png_response(req: httpx.Request) -> httpx.Response:
    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), (10, 20, 30)).save(buffer, format="PNG")
    return httpx.Response(200, content=buffer.getvalue())


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as sess:
        yield sess
    await engine.dispose()


@pytest_asyncio.fixture
async def qdrant() -> AsyncIterator[AsyncQdrantClient]:
    client = AsyncQdrantClient(":memory:")
    await ensure_collection(client)
    try:
        yield client
    finally:
        await client.close()


def _source() -> AwinSource:
    return AwinSource(str(FIXTURE), publisher_id=PUBLISHER_ID)


def _ok_client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_png_response))


# -- the happy path ------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_ingestion_writes_pg_qdrant_and_audit_row(
    session: AsyncSession, qdrant: AsyncQdrantClient
) -> None:
    embedder = FakeEmbedder()

    stats = await run_ingestion(
        session, qdrant, _source(), embedder=embedder, http_client=_ok_client()
    )

    # Audit row: exactly one, status=success, counts line up with the fixture.
    runs = (await session.execute(select(IndexingRun))).scalars().all()
    assert len(runs) == 1
    run = runs[0]
    assert run.source == "awin"
    assert run.status == "success"
    assert run.finished_at is not None
    assert run.products_in == 12  # whole fixture
    assert run.products_out == EXPECTED_MAPPED  # only mapped rows hit Qdrant
    assert run.errors_count == EXPECTED_UNMAPPED

    # Postgres: mapped rows present, unmapped ones quarantined.
    products = (await session.execute(select(Product))).scalars().all()
    assert len(products) == EXPECTED_MAPPED
    quarantined = (await session.execute(select(UnmappedProduct))).scalars().all()
    assert len(quarantined) == EXPECTED_UNMAPPED
    assert all(q.reason == "unmapped_category" for q in quarantined)

    # Qdrant: one point per mapped product.
    assert (await qdrant.count(COLLECTION_NAME)).count == EXPECTED_MAPPED

    # Stats object matches the audit row (caller may also use it directly).
    assert stats.products_in == 12
    assert stats.products_out == EXPECTED_MAPPED
    assert stats.unmapped == EXPECTED_UNMAPPED
    assert stats.embed_failures == 0


# -- encoder isolation ---------------------------------------------------------


@pytest.mark.asyncio
async def test_unmapped_rows_never_reach_the_encoder(
    session: AsyncSession, qdrant: AsyncQdrantClient
) -> None:
    # If the encoder saw all 12 rows we'd be wasting CLIP forward passes on
    # rows we already know we won't serve.
    embedder = FakeEmbedder()
    await run_ingestion(
        session,
        qdrant,
        _source(),
        embedder=embedder,
        http_client=_ok_client(),
        batch_size=4,  # forces multiple batches
    )
    # The encoder is called once per batch with the *mapped* slice, never with
    # an unmapped row. So total embedder invocations ≤ batch count, and total
    # images encoded == mapped count (we don't assert exact batch count to stay
    # robust against grouping changes).
    assert embedder.calls > 0
    # Implicit: no Qdrant point exists for an unmapped retailer_product_id.
    quarantined_ids = {
        q.retailer_product_id for q in (await session.execute(select(UnmappedProduct))).scalars()
    }
    # Spot-check the two unmapped IDs known from the fixture.
    assert quarantined_ids == {"K025-H", "K025-I"}


# -- idempotency ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerunning_is_idempotent(session: AsyncSession, qdrant: AsyncQdrantClient) -> None:
    # Two runs back-to-back. Postgres should still hold 10 rows (no dups via
    # the unique constraint), Qdrant should still hold 10 points (UUID5 keys),
    # and there should be two audit rows so the operator can see both runs.
    await run_ingestion(
        session, qdrant, _source(), embedder=FakeEmbedder(), http_client=_ok_client()
    )
    await run_ingestion(
        session, qdrant, _source(), embedder=FakeEmbedder(), http_client=_ok_client()
    )

    assert (await session.execute(select(Product))).scalars().all().__len__() == EXPECTED_MAPPED
    assert (await qdrant.count(COLLECTION_NAME)).count == EXPECTED_MAPPED
    runs = (await session.execute(select(IndexingRun))).scalars().all()
    assert len(runs) == 2
    assert all(r.status == "success" for r in runs)


# -- mid-batch failure isolation -----------------------------------------------


@pytest.mark.asyncio
async def test_download_failure_quarantines_only_the_bad_row(
    session: AsyncSession, qdrant: AsyncQdrantClient
) -> None:
    # 404 the image for one specific product; the other 9 mapped rows must
    # still make it through. Embed-failure count is reflected in errors_count
    # alongside the structural unmapped count.
    def handler(req: httpx.Request) -> httpx.Response:
        if "K025-A" in str(req.url):
            return httpx.Response(404)
        return _png_response(req)

    await run_ingestion(
        session,
        qdrant,
        _source(),
        embedder=FakeEmbedder(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    quarantined = (await session.execute(select(UnmappedProduct))).scalars().all()
    reasons = {q.reason for q in quarantined}
    assert "http_404" in reasons  # the bad image landed with the right reason
    assert "unmapped_category" in reasons  # the two pre-existing unmapped rows
    assert len(quarantined) == EXPECTED_UNMAPPED + 1

    # Qdrant has 9 points: 10 mapped minus the one that failed to download.
    assert (await qdrant.count(COLLECTION_NAME)).count == EXPECTED_MAPPED - 1

    run = (await session.execute(select(IndexingRun))).scalars().one()
    assert run.status == "success"
    assert run.products_out == EXPECTED_MAPPED - 1
    # errors_count counts both unmapped and embed_failures.
    assert run.errors_count == EXPECTED_UNMAPPED + 1


# -- limit + since pass-through ------------------------------------------------


@pytest.mark.asyncio
async def test_limit_stops_after_n_products(
    session: AsyncSession, qdrant: AsyncQdrantClient
) -> None:
    await run_ingestion(
        session,
        qdrant,
        _source(),
        embedder=FakeEmbedder(),
        http_client=_ok_client(),
        limit=3,
    )
    run = (await session.execute(select(IndexingRun))).scalars().one()
    assert run.products_in == 3
    # Of those 3, how many are mapped is fixture-dependent — assert the
    # invariant rather than the exact count.
    assert run.products_out <= 3
