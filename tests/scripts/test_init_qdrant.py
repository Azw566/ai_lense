"""Tests for the Qdrant collection initializer (P1-T5).

Runs against an in-memory Qdrant client — same code path as production for
collection + index creation, no docker required. The four contracts under test:

1. First run on an empty cluster creates the collection with the right vector
   shape and distance metric.
2. Re-running is idempotent — second invocation neither errors nor changes the
   collection (the explicit ask in the roadmap's "Done when").
3. The two payload indexes called out in arch §1 are present and keyword-typed
   — without them, filter+ANN does a brute scan at query time.
4. `--recreate` actually drops and rebuilds (covered with a tiny smoke).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from scripts.init_qdrant import (
    COLLECTION_NAME,
    HNSW_EF_CONSTRUCT,
    HNSW_M,
    INDEXED_PAYLOAD_FIELDS,
    ensure_collection,
)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncQdrantClient]:
    qdrant = AsyncQdrantClient(":memory:")
    try:
        yield qdrant
    finally:
        await qdrant.close()


@pytest.mark.asyncio
async def test_creates_collection_with_expected_shape(client: AsyncQdrantClient) -> None:
    await ensure_collection(client)

    info = await client.get_collection(COLLECTION_NAME)
    vectors_config = info.config.params.vectors
    # Single (default) vector — `.size` and `.distance` are top-level.
    assert vectors_config.size == 512
    assert vectors_config.distance.value.lower() == "cosine"


@pytest.mark.asyncio
async def test_ensure_collection_is_idempotent(client: AsyncQdrantClient) -> None:
    await ensure_collection(client)
    # Re-running must not raise nor produce a different collection state.
    await ensure_collection(client)

    collections = await client.get_collections()
    names = [c.name for c in collections.collections]
    # Exactly one collection — no shadow / dup created on second invocation.
    assert names.count(COLLECTION_NAME) == 1


@pytest.mark.asyncio
async def test_required_payload_indexes_are_requested(
    client: AsyncQdrantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The in-memory Qdrant client ignores payload indexes (verified by its
    # UserWarning), so we can't inspect them via `get_collection`. Spy on the
    # call instead — this proves *our* code asks for the right indexes; the
    # server's contract is exercised against real Qdrant in the deploy smoke.
    captured: list[tuple[str, str]] = []

    real_create = client.create_payload_index

    async def spy(**kwargs):
        captured.append((kwargs["field_name"], kwargs["field_schema"].value))
        return await real_create(**kwargs)

    monkeypatch.setattr(client, "create_payload_index", spy)
    await ensure_collection(client)

    assert captured == [(name, schema.value) for name, schema in INDEXED_PAYLOAD_FIELDS]


@pytest.mark.asyncio
async def test_recreate_drops_and_rebuilds(client: AsyncQdrantClient) -> None:
    from qdrant_client.http import models as qm

    await ensure_collection(client)
    # Insert one dummy point so we can detect whether the collection was dropped.
    await client.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            qm.PointStruct(
                id="11111111-1111-1111-1111-111111111111",
                vector=[0.0] * 512,
                payload={"category": "couch", "retailer": "awin"},
            )
        ],
        wait=True,
    )
    assert (await client.count(COLLECTION_NAME)).count == 1

    await ensure_collection(client, recreate=True)
    assert (await client.count(COLLECTION_NAME)).count == 0


def test_hnsw_constants_match_architecture_doc() -> None:
    # Roadmap pins these (P1-T5) — guard against silent drift.
    assert HNSW_M == 16
    assert HNSW_EF_CONSTRUCT == 128
