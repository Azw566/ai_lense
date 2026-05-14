"""The `ProductSource` abstraction (arch §5.2).

Every affiliate retailer (Awin now; Amazon, IKEA later) implements this one
interface. The ingestion pipeline only ever talks to `ProductSource` — adding a
retailer is one new class, zero pipeline changes. If pipeline code ever grows an
`if retailer == "awin"`, that logic belongs in a source instead.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(slots=True)
class RawProduct:
    """A normalized product from any source — the unit the pipeline operates on.

    `category` is the canonical taxonomy category (P1-T2) or None when the
    source category didn't map; `source_category` is kept verbatim so unmapped
    rows can be quarantined and reviewed. `raw_payload` is the original source
    row, kept for re-normalization without re-fetching (arch §5.3).
    """

    retailer: str
    retailer_product_id: str
    title: str
    source_category: str | None
    category: str | None
    price: Decimal | None
    currency: str
    image_url: str
    product_url: str
    affiliate_url: str
    brand: str | None = None
    raw_payload: dict[str, str] = field(default_factory=dict)

    @property
    def is_mapped(self) -> bool:
        """True when the source category resolved to one of our categories."""
        return self.category is not None


class ProductSource(ABC):
    """Interface every affiliate source implements (arch §5.2)."""

    #: Stable retailer key, also used as the `retailer` column / taxonomy namespace.
    retailer: str

    @abstractmethod
    def list_products(self, since: datetime | None = None) -> Iterator[RawProduct]:
        """Yield normalized products. `since` is a best-effort incremental filter
        (a source with no per-row timestamp simply ignores it)."""

    @abstractmethod
    def get_product(self, product_id: str) -> RawProduct | None:
        """Fetch a single product by its `retailer_product_id`, or None."""

    @abstractmethod
    def build_affiliate_url(self, identifier: str) -> str:
        """Build the click-tracked affiliate URL. `identifier` is source-defined
        (Awin: the raw deep link; Amazon: the ASIN)."""

    @property
    @abstractmethod
    def source_category_map(self) -> dict[str, str]:
        """Normalized source-category string -> our canonical category."""
