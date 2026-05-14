"""Tests for the Awin CSV ProductSource (P1-T3)."""

import gzip
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.catalog.base import ProductSource, RawProduct
from app.catalog.sources.awin import AwinSource

FIXTURE = Path(__file__).parent / "fixtures" / "awin_sample.csv"
PUBLISHER_ID = "123456"


@pytest.fixture
def source() -> AwinSource:
    return AwinSource(str(FIXTURE), publisher_id=PUBLISHER_ID)


def test_awin_source_is_a_product_source(source: AwinSource) -> None:
    assert isinstance(source, ProductSource)
    assert source.retailer == "awin"


def test_list_products_parses_all_rows(source: AwinSource) -> None:
    products = list(source.list_products())
    assert len(products) == 12
    assert all(isinstance(p, RawProduct) for p in products)


def test_normalization_of_a_known_row(source: AwinSource) -> None:
    products = {p.retailer_product_id: p for p in source.list_products()}
    sofa = products["K025-A"]
    assert sofa.title == "Malmo 3-Seater Sofa Grey"
    assert sofa.category == "couch"
    assert sofa.source_category == "Sofas"
    assert sofa.price == Decimal("599.00")
    assert sofa.currency == "GBP"
    assert sofa.brand == "Habitat"
    assert sofa.image_url == "https://img.example.com/K025-A.jpg"
    assert sofa.is_mapped
    # raw_payload keeps the original row for later re-normalization.
    assert sofa.raw_payload["merchant_category"] == "Sofas"


def test_breadcrumb_category_maps(source: AwinSource) -> None:
    products = {p.retailer_product_id: p for p in source.list_products()}
    table = products["K025-C"]
    assert table.source_category == "Home & Garden > Furniture > Dining Tables"
    assert table.category == "dining_table"


def test_mapping_rate_exceeds_p1t3_threshold(source: AwinSource) -> None:
    products = list(source.list_products())
    mapped = sum(p.is_mapped for p in products)
    rate = mapped / len(products)
    # P1-T3 definition-of-done: >70% of feed rows resolve to our taxonomy.
    assert rate > 0.70
    # The fixture intentionally contains 2 unmapped rows (garden / kitchen).
    assert mapped == 10


def test_unmapped_rows_keep_source_category(source: AwinSource) -> None:
    products = {p.retailer_product_id: p for p in source.list_products()}
    garden = products["K025-H"]
    assert garden.category is None
    assert not garden.is_mapped
    assert garden.source_category == "Garden Furniture"


def test_build_affiliate_url_forces_publisher_id_and_clickref(source: AwinSource) -> None:
    deep_link = (
        "https://www.awin1.com/cread.php?awinmid=6789&awinaffid=999999"
        "&clickref=&ued=https%3A%2F%2Fshop.example.com%2Fp%2FK025-A"
    )
    url = source.build_affiliate_url(deep_link)
    query = parse_qs(urlsplit(url).query)
    # Our publisher id overrides whatever the feed shipped with.
    assert query["awinaffid"] == [PUBLISHER_ID]
    assert query["clickref"] == ["visual-deco-search"]
    # The destination url survives the rewrite.
    assert query["ued"] == ["https://shop.example.com/p/K025-A"]


def test_build_affiliate_url_empty_input_returns_empty(source: AwinSource) -> None:
    assert source.build_affiliate_url("") == ""


def test_affiliate_url_applied_during_listing(source: AwinSource) -> None:
    product = next(iter(source.list_products()))
    query = parse_qs(urlsplit(product.affiliate_url).query)
    assert query["awinaffid"] == [PUBLISHER_ID]


def test_source_category_map_exposes_awin_aliases(source: AwinSource) -> None:
    mapping = source.source_category_map
    assert mapping["sofas"] == "couch"
    assert mapping["office desks"] == "desk"
    assert "garden furniture" not in mapping


def test_since_filter_skips_older_rows(source: AwinSource) -> None:
    # Fixture rows span 2024-01-20 .. 2024-03-12.
    recent = list(source.list_products(since=datetime(2024, 3, 1)))
    assert {p.retailer_product_id for p in recent} == {
        "K025-A",
        "K025-B",
        "K025-D",
        "K025-F",
        "K025-G",
        "K025-H",
        "K025-J",
        "K025-K",
        "K025-L",
    }


def test_get_product_finds_by_id(source: AwinSource) -> None:
    product = source.get_product("K025-E")
    assert product is not None
    assert product.title == "Berber Wool Rug 160x230"
    assert source.get_product("does-not-exist") is None


def test_rows_missing_id_or_title_are_skipped(tmp_path: Path) -> None:
    csv_path = tmp_path / "broken.csv"
    csv_path.write_text(
        "aw_deep_link,product_name,merchant_product_id,merchant_category,search_price\n"
        "http://x/1,Good Sofa,ID1,Sofas,100.00\n"
        "http://x/2,,ID2,Sofas,100.00\n"  # missing title
        "http://x/3,No Id Product,,Sofas,100.00\n"  # missing id
    )
    products = list(AwinSource(str(csv_path), publisher_id=PUBLISHER_ID).list_products())
    assert len(products) == 1
    assert products[0].retailer_product_id == "ID1"


def test_gzipped_feed_is_read_transparently(tmp_path: Path) -> None:
    gz_path = tmp_path / "feed.csv.gz"
    with gzip.open(gz_path, "wt", encoding="utf-8") as fh:
        fh.write(FIXTURE.read_text(encoding="utf-8"))
    products = list(AwinSource(str(gz_path), publisher_id=PUBLISHER_ID).list_products())
    assert len(products) == 12


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("299.00", Decimal("299.00")),  # plain anglophone
        ("£1,299.99", Decimal("1299.99")),  # anglophone thousands + symbol
        ("1,299.99", Decimal("1299.99")),  # anglophone thousands
        ("1.299,99", Decimal("1299.99")),  # European: dot=thousands, comma=decimal
        ("299,00", Decimal("299.00")),  # European decimal comma
        ("1.299.999", Decimal("1299999")),  # European thousands, no decimals
        ("1,299", Decimal("1299")),  # ambiguous single comma, 3 trailing -> thousands
        ("49,5", Decimal("49.5")),  # single comma, <=2 trailing -> decimal
        ("", None),
        ("n/a", None),
    ],
)
def test_price_parsing(raw: str, expected: Decimal | None) -> None:
    from app.catalog.sources.awin import _parse_price

    assert _parse_price(raw) == expected


# -- URL streaming path (the production path) ----------------------------------


def _url_source(body: bytes, *, gzip_body: bool = False) -> AwinSource:
    """An AwinSource backed by a mocked HTTP feed, to exercise the URL path."""
    payload = gzip.compress(body) if gzip_body else body

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return AwinSource("https://feed.example.com/awin.csv", PUBLISHER_ID, http_client=client)


def test_url_path_parses_quoted_field_with_embedded_newline() -> None:
    # A quoted field containing a newline must NOT be split into two rows.
    body = (
        b"merchant_product_id,product_name,merchant_category,search_price\r\n"
        b'ID1,"Malmo Sofa\nwith newline in name",Sofas,599.00\r\n'
        b"ID2,Plain Chair,Dining Chairs,49.00\r\n"
    )
    products = list(_url_source(body).list_products())
    assert len(products) == 2
    assert products[0].title == "Malmo Sofa\nwith newline in name"
    assert products[0].category == "couch"
    assert products[1].retailer_product_id == "ID2"


def test_url_path_decodes_utf8_correctly() -> None:
    body = (
        "merchant_product_id,product_name,merchant_category,brand_name,search_price\r\n"
        "ID1,Canapé Malmö,Sofas,Habitât,599.00\r\n"
    ).encode()
    product = next(iter(_url_source(body).list_products()))
    assert product.title == "Canapé Malmö"
    assert product.brand == "Habitât"


def test_url_path_reads_gzipped_feed() -> None:
    body = FIXTURE.read_bytes()
    products = list(_url_source(body, gzip_body=True).list_products())
    assert len(products) == 12


# -- malformed-row resilience (arch §5.1: one bad row must not crash the feed) --


def test_nul_byte_row_is_skipped_not_crashing(tmp_path: Path) -> None:
    csv_path = tmp_path / "nul.csv"
    csv_path.write_bytes(
        b"merchant_product_id,product_name,merchant_category,search_price\n"
        b"ID1,Good Sofa,Sofas,100.00\n"
        b"ID2,Bad\x00Row,Sofas,100.00\n"
        b"ID3,Another Sofa,Sofas,100.00\n"
    )
    products = list(AwinSource(str(csv_path), publisher_id=PUBLISHER_ID).list_products())
    ids = {p.retailer_product_id for p in products}
    # The NUL row is dropped; the rows around it still come through.
    assert "ID1" in ids and "ID3" in ids
    assert "ID2" not in ids


def test_oversized_field_does_not_crash_feed(tmp_path: Path) -> None:
    # A 200 KB description blows past csv's 128 KB default limit; the raised
    # field_size_limit must let it through rather than aborting the feed.
    big_description = "x" * 200_000
    csv_path = tmp_path / "big.csv"
    csv_path.write_text(
        "merchant_product_id,product_name,description,merchant_category,search_price\n"
        f"ID1,Big Sofa,{big_description},Sofas,100.00\n"
        "ID2,Small Sofa,short,Sofas,100.00\n"
    )
    products = list(AwinSource(str(csv_path), publisher_id=PUBLISHER_ID).list_products())
    assert {p.retailer_product_id for p in products} == {"ID1", "ID2"}
