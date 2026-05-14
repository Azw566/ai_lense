"""SQLAlchemy ORM models for the catalog ingestion pipeline.

Four tables (arch §5):
  - products          — the catalog itself; source of truth, mirrored into Qdrant.
  - indexing_runs     — audit log, one row per ingestion run (arch §5.1).
  - unmapped_products — quarantine queue for rows that failed normalization or embedding.
  - embedding_cache   — keyed by sha256(image_url) so partial re-runs skip recompute (arch §5.5).

JSONB / native UUID are used on Postgres; `with_variant` keeps the models usable
on SQLite for fast unit tests.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# JSONB on Postgres, plain JSON elsewhere (SQLite test runs).
JsonB = JSON().with_variant(JSONB(), "postgresql")


class Product(Base):
    """A normalized catalog product. Unique per (retailer, retailer_product_id)."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint(
            "retailer", "retailer_product_id", name="uq_products_retailer_product"
        ),
        Index("ix_products_retailer_product_id", "retailer_product_id"),
        Index("ix_products_category", "category"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    retailer: Mapped[str] = mapped_column(String(64))
    retailer_product_id: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str] = mapped_column(String(64))
    price_eur: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    image_url: Mapped[str] = mapped_column(Text)
    product_url: Mapped[str] = mapped_column(Text)
    raw_payload: Mapped[dict] = mapped_column(JsonB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_price_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class IndexingRun(Base):
    """One row per ingestion run — the first thing to query when a pipeline degrades."""

    __tablename__ = "indexing_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="running")
    products_in: Mapped[int] = mapped_column(Integer, default=0)
    products_out: Mapped[int] = mapped_column(Integer, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class UnmappedProduct(Base):
    """Quarantine queue: rows whose source category didn't map, or that failed to embed.

    Ingestion never blocks on a bad row — it lands here with a reason and moves on.
    """

    __tablename__ = "unmapped_products"
    __table_args__ = (
        Index("ix_unmapped_products_retailer", "retailer"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    retailer: Mapped[str] = mapped_column(String(64))
    retailer_product_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(String(255))
    raw_payload: Mapped[dict] = mapped_column(JsonB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EmbeddingCache(Base):
    """Cache of CLIP embeddings keyed by sha256(image_url) (arch §5.5).

    A 512-d float32 vector is ~2 KB packed; the table stays small even at 1M products.
    """

    __tablename__ = "embedding_cache"

    image_url_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Contract for P1-T4: 512 × float32, little-endian, np.ndarray.tobytes() (2048 bytes).
    embedding: Mapped[bytes] = mapped_column(LargeBinary)
    model_version: Mapped[str] = mapped_column(String(64))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
