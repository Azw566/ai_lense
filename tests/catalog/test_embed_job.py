"""Tests for the catalog embed job (P1-T4).

Exercises the three responsibilities in `app/catalog/embed_job.py`:

1. Cache hit → no download, no model call.
2. Cache miss → download, embed, write back to `embedding_cache`.
3. Failure (404, decode, missing url) → row lands in `unmapped_products`, the
   surrounding batch still succeeds.

Uses an in-memory SQLite session and a stand-in `CLIPEmbedder` that returns
deterministic vectors — running real CLIP here would make this test cost
seconds for no extra coverage."""

from __future__ import annotations

import io
from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import numpy as np
import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.catalog.base import RawProduct
from app.catalog.embed_job import ProductWithEmbedding, embed_products
from app.db.base import Base
from app.db.models import EmbeddingCache, UnmappedProduct
from app.services.embedding import (
    EMBED_DIM,
    MODEL_VERSION,
    embedding_to_bytes,
    sha256_of,
)


class FakeEmbedder:
    """Deterministic stand-in for `CLIPEmbedder` — returns a unit-norm vector
    derived from the image's first pixel, and tracks how often it was called."""

    model_version = MODEL_VERSION

    def __init__(self) -> None:
        self.calls = 0

    def embed_pil_images(self, images: list[Image.Image]) -> np.ndarray:
        self.calls += 1
        vectors = []
        for image in images:
            pixel = image.getpixel((0, 0))
            seed = sum(pixel) if isinstance(pixel, tuple) else int(pixel)
            rng = np.random.default_rng(seed)
            vec = rng.standard_normal(EMBED_DIM).astype(np.float32)
            vec /= np.linalg.norm(vec)
            vectors.append(vec)
        return np.stack(vectors) if vectors else np.zeros((0, EMBED_DIM), dtype=np.float32)


def _png(color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _make_product(product_id: str, image_url: str = "https://img.example.com/x.png") -> RawProduct:
    return RawProduct(
        retailer="awin",
        retailer_product_id=product_id,
        title=f"Product {product_id}",
        source_category="Sofas",
        category="couch",
        price=Decimal("199.00"),
        currency="EUR",
        image_url=image_url,
        product_url="https://shop.example.com/p",
        affiliate_url="https://awin1.com/cread.php?awinaffid=1&ued=...",
        brand=None,
        raw_payload={"merchant_product_id": product_id},
    )


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as sess:
        yield sess
    await engine.dispose()


def _ok_client(body: bytes = _png()) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    )


# -- cache-miss path ------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_miss_downloads_embeds_and_persists(session: AsyncSession) -> None:
    embedder = FakeEmbedder()
    products = [_make_product("P1"), _make_product("P2", "https://img.example.com/p2.png")]
    client = _ok_client()

    results = await embed_products(session, products, embedder=embedder, http_client=client)
    await session.commit()

    assert len(results) == 2
    assert all(isinstance(r, ProductWithEmbedding) for r in results)
    assert all(not r.cache_hit for r in results)
    assert embedder.calls == 1  # one batch encode for both images

    cached = (await session.execute(select(EmbeddingCache))).scalars().all()
    assert {row.image_url_sha256 for row in cached} == {
        sha256_of(products[0].image_url),
        sha256_of(products[1].image_url),
    }
    assert all(row.model_version == MODEL_VERSION for row in cached)


# -- cache-hit path -------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_skips_download_and_embed(session: AsyncSession) -> None:
    product = _make_product("P1")
    image_hash = sha256_of(product.image_url)
    seed_vector = np.ones(EMBED_DIM, dtype=np.float32) / np.sqrt(EMBED_DIM)
    session.add(
        EmbeddingCache(
            image_url_sha256=image_hash,
            embedding=embedding_to_bytes(seed_vector),
            model_version=MODEL_VERSION,
        )
    )
    await session.flush()

    embedder = FakeEmbedder()
    # No http_client provided — a real network call would fail the test, proving
    # the cache short-circuit.
    results = await embed_products(
        session,
        [product],
        embedder=embedder,
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda req: pytest.fail("download must not happen"))  # type: ignore[func-returns-value]
        ),
    )

    assert len(results) == 1
    assert results[0].cache_hit is True
    np.testing.assert_allclose(results[0].embedding, seed_vector)
    assert embedder.calls == 0


@pytest.mark.asyncio
async def test_cache_keyed_on_model_version(session: AsyncSession) -> None:
    # A row from a *different* model version must be ignored — mixing two
    # encoders' vectors in Qdrant is the silent-corruption failure mode.
    product = _make_product("P1")
    image_hash = sha256_of(product.image_url)
    session.add(
        EmbeddingCache(
            image_url_sha256=image_hash,
            embedding=embedding_to_bytes(np.zeros(EMBED_DIM, dtype=np.float32)),
            model_version="some-older-model",
        )
    )
    await session.flush()

    embedder = FakeEmbedder()
    results = await embed_products(session, [product], embedder=embedder, http_client=_ok_client())
    assert len(results) == 1
    assert results[0].cache_hit is False
    assert embedder.calls == 1


# -- failure isolation ---------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_download_quarantines_to_unmapped_products(session: AsyncSession) -> None:
    products = [_make_product("OK"), _make_product("BAD", "https://img.example.com/missing.png")]

    def handler(req: httpx.Request) -> httpx.Response:
        if "missing" in str(req.url):
            return httpx.Response(404)
        return httpx.Response(200, content=_png())

    embedder = FakeEmbedder()
    results = await embed_products(
        session,
        products,
        embedder=embedder,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    await session.commit()

    assert {r.product.retailer_product_id for r in results} == {"OK"}
    quarantined = (await session.execute(select(UnmappedProduct))).scalars().all()
    assert len(quarantined) == 1
    assert quarantined[0].retailer_product_id == "BAD"
    assert quarantined[0].reason == "http_404"


@pytest.mark.asyncio
async def test_missing_image_url_quarantined_without_http_call(session: AsyncSession) -> None:
    product = _make_product("NOURL", image_url="")

    def boom(req: httpx.Request) -> httpx.Response:
        pytest.fail("download must not be attempted for empty url")

    results = await embed_products(
        session,
        [product],
        embedder=FakeEmbedder(),
        http_client=httpx.Client(transport=httpx.MockTransport(boom)),
    )
    await session.commit()

    assert results == []
    quarantined = (await session.execute(select(UnmappedProduct))).scalars().all()
    assert len(quarantined) == 1
    assert quarantined[0].reason == "missing_image_url"


@pytest.mark.asyncio
async def test_empty_input_returns_empty(session: AsyncSession) -> None:
    results = await embed_products(session, [], embedder=FakeEmbedder())
    assert results == []
