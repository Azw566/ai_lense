"""Awin CSV datafeed source (roadmap P1-T3).

Awin's "Create-a-Feed" exports a (usually gzip-compressed) CSV snapshot of a
merchant catalogue. This module streams that file row-by-row — never loading the
whole body into memory, since feeds run to hundreds of MB — normalizes each row
into a `RawProduct`, and maps the source category through the taxonomy (P1-T2).

Affiliate links: Awin deep links are `awin1.com/cread.php?...` redirects.
`build_affiliate_url` forces our `awinaffid` (publisher id) onto the link and
adds a `clickref` so clicks are attributable in the Awin dashboard.
"""

from __future__ import annotations

import csv
import gzip
import io
from collections.abc import Iterable, Iterator
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.catalog.base import ProductSource, RawProduct
from app.core.logging import get_logger
from app.services.taxonomy import Taxonomy, default_taxonomy

logger = get_logger(__name__)

# Awin descriptions can be long HTML blobs that blow past csv's 128 KB default
# field limit. Raise it so a fat field doesn't abort the whole feed (arch §5.1).
csv.field_size_limit(4 * 1024 * 1024)

# Awin "Create-a-Feed" column names. Feeds are configurable, so for each logical
# field we try a few known column names in priority order and take the first set.
_ID_COLUMNS = ("merchant_product_id", "aw_product_id")
_TITLE_COLUMNS = ("product_name",)
_BRAND_COLUMNS = ("brand_name",)
_CATEGORY_COLUMNS = (
    "merchant_category",
    "merchant_product_category_path",
    "category_name",
)
_PRICE_COLUMNS = ("search_price", "store_price", "display_price", "base_price")
_CURRENCY_COLUMNS = ("currency",)
_IMAGE_COLUMNS = ("merchant_image_url", "aw_image_url", "large_image")
_DEEP_LINK_COLUMNS = ("aw_deep_link", "merchant_deep_link")
_UPDATED_COLUMNS = ("last_updated",)

# Accepted formats for the `last_updated` column, used by the `since` filter.
_DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")

_GZIP_MAGIC = b"\x1f\x8b"


def _first(row: dict[str, str], columns: Iterable[str]) -> str:
    """First non-empty value among `columns`, stripped; '' if none present."""
    for col in columns:
        value = (row.get(col) or "").strip()
        if value:
            return value
    return ""


def _parse_price(raw: str) -> Decimal | None:
    """Parse a feed price string to Decimal, or None.

    Handles both Anglophone and European number formats — EU merchants on Awin
    routinely write '1.299,99' or '299,00'. Rule: when both separators are
    present, the *last* one is the decimal point; when only ',' is present, it's
    a decimal point only if there's a single one with <=2 trailing digits.
    """
    if not raw:
        return None
    cleaned = "".join(ch for ch in raw if ch.isdigit() or ch in ".,")
    if not cleaned:
        return None

    has_dot = "." in cleaned
    has_comma = "," in cleaned
    if has_dot and has_comma:
        if cleaned.rfind(",") > cleaned.rfind("."):  # '1.299,99' — comma is decimal
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:  # '1,299.99' — dot is decimal
            cleaned = cleaned.replace(",", "")
    elif has_comma:
        if cleaned.count(",") == 1 and len(cleaned.rsplit(",", 1)[1]) <= 2:
            cleaned = cleaned.replace(",", ".")  # '299,00' — decimal comma
        else:
            cleaned = cleaned.replace(",", "")  # '1,299' — thousands separator
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")  # '1.299.999' — all dots are grouping

    try:
        return Decimal(cleaned) if cleaned else None
    except InvalidOperation:
        return None


def _parse_datetime(raw: str) -> datetime | None:
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


class _ByteIteratorIO(io.RawIOBase):
    """Adapts an iterator of byte chunks (httpx `iter_bytes()`) into a readable
    binary stream, so the `csv` module can do its own line-splitting — critical
    for correctly handling quoted fields that contain newlines."""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = iter(chunks)
        self._buffer = b""

    def readable(self) -> bool:
        return True

    def readinto(self, target: object) -> int:
        view = memoryview(target).cast("B")  # type: ignore[arg-type]
        while not self._buffer:
            try:
                self._buffer = next(self._chunks)
            except StopIteration:
                return 0
        n = min(len(view), len(self._buffer))
        view[:n] = self._buffer[:n]
        self._buffer = self._buffer[n:]
        return n


def _wrap_byte_stream(byte_chunks: Iterator[bytes]) -> io.TextIOWrapper:
    """Wrap a byte-chunk stream as a decoded text stream, transparently
    un-gzipping. Gzip is detected from the leading magic bytes (via buffered
    peek, so it's robust to tiny first chunks), not the URL suffix.
    `TextIOWrapper` decodes incrementally, so multi-byte chars never get
    corrupted across chunk boundaries.
    """
    buffered = io.BufferedReader(_ByteIteratorIO(byte_chunks))
    stream: io.BufferedIOBase
    if buffered.peek(2)[:2] == _GZIP_MAGIC:
        stream = gzip.GzipFile(fileobj=buffered)
    else:
        stream = buffered
    return io.TextIOWrapper(stream, encoding="utf-8", errors="replace", newline="")


class AwinSource(ProductSource):
    """Streams one Awin CSV datafeed and yields normalized `RawProduct`s."""

    retailer = "awin"

    def __init__(
        self,
        feed: str,
        publisher_id: str,
        *,
        taxonomy: Taxonomy = default_taxonomy,
        click_ref: str = "visual-deco-search",
        http_client: httpx.Client | None = None,
        timeout: float = 60.0,
    ) -> None:
        """`feed` is an HTTP(S) URL or a local file path (the latter is handy for
        the inspect script and tests). `publisher_id` is the Awin `awinaffid`."""
        self._feed = feed
        self._publisher_id = publisher_id
        self._taxonomy = taxonomy
        self._click_ref = click_ref
        self._http_client = http_client
        self._timeout = timeout

    # -- ProductSource interface ------------------------------------------------

    def list_products(self, since: datetime | None = None) -> Iterator[RawProduct]:
        rows_in = 0
        rows_out = 0
        for row in self._iter_rows():
            rows_in += 1
            product = self._row_to_product(row, since)
            if product is not None:
                rows_out += 1
                yield product
        logger.info(
            "awin.list_products.done",
            feed=self._feed,
            rows_in=rows_in,
            rows_out=rows_out,
        )

    def get_product(self, product_id: str) -> RawProduct | None:
        # A CSV feed has no index — this is a linear scan. Fine for the rare
        # single-product lookup; never call it in a hot loop.
        for product in self.list_products():
            if product.retailer_product_id == product_id:
                return product
        return None

    def build_affiliate_url(self, identifier: str) -> str:
        """Force our `awinaffid` and a `clickref` onto an Awin deep link."""
        if not identifier:
            return ""
        parts = urlsplit(identifier)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["awinaffid"] = self._publisher_id
        if not query.get("clickref"):
            query["clickref"] = self._click_ref
        return urlunsplit(parts._replace(query=urlencode(query)))

    @property
    def source_category_map(self) -> dict[str, str]:
        return self._taxonomy.alias_map(self.retailer)

    # -- internals --------------------------------------------------------------

    def _iter_rows(self) -> Iterator[dict[str, str]]:
        """Yield raw CSV rows as dicts, streaming the feed (local file or URL)."""
        path = Path(self._feed)
        if path.exists():
            opener = gzip.open if self._feed.endswith(".gz") else open
            with opener(path, "rt", encoding="utf-8", errors="replace", newline="") as fh:
                yield from self._read_csv(fh)
            return

        client = self._http_client or httpx.Client(timeout=self._timeout)
        try:
            with client.stream("GET", self._feed) as response:
                response.raise_for_status()
                yield from self._read_csv(_wrap_byte_stream(response.iter_bytes()))
        finally:
            if self._http_client is None:
                client.close()

    @staticmethod
    def _read_csv(text_stream: Iterable[str]) -> Iterator[dict[str, str]]:
        """Yield rows from a CSV text stream, surviving malformed rows.

        A `csv.Error` (NUL byte, oversized field, broken quoting) on one row is
        logged and skipped — one bad row must never abort the feed (arch §5.1).
        """
        reader = csv.DictReader(text_stream)
        while True:
            try:
                row = next(reader)
            except StopIteration:
                return
            except csv.Error as exc:
                logger.warning("awin.row.skipped", reason="csv_error", error=str(exc))
                continue
            yield row

    def _row_to_product(
        self, row: dict[str, str], since: datetime | None
    ) -> RawProduct | None:
        """Normalize one CSV row. Returns None for rows that should be skipped."""
        product_id = _first(row, _ID_COLUMNS)
        title = _first(row, _TITLE_COLUMNS)
        if not product_id or not title:
            # A row with no stable id or no title is unusable — skip, don't crash
            # the feed. Genuine data-quality issues surface in the run audit.
            logger.warning(
                "awin.row.skipped",
                reason="missing_id_or_title",
                product_id=product_id or None,
            )
            return None

        if since is not None:
            updated = _parse_datetime(_first(row, _UPDATED_COLUMNS))
            if updated is not None and updated < since:
                return None

        source_category = _first(row, _CATEGORY_COLUMNS) or None
        category = self._taxonomy.map_source_category(self.retailer, source_category)
        deep_link = _first(row, _DEEP_LINK_COLUMNS)

        return RawProduct(
            retailer=self.retailer,
            retailer_product_id=product_id,
            title=title,
            source_category=source_category,
            category=category,
            price=_parse_price(_first(row, _PRICE_COLUMNS)),
            currency=_first(row, _CURRENCY_COLUMNS).upper(),
            image_url=_first(row, _IMAGE_COLUMNS),
            product_url=deep_link,
            affiliate_url=self.build_affiliate_url(deep_link),
            brand=_first(row, _BRAND_COLUMNS) or None,
            raw_payload=dict(row),
        )
