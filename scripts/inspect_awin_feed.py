"""Dev utility: sanity-check an Awin feed before wiring it into ingestion.

Prints the first few normalized products, the taxonomy mapping rate, and the
category distribution — including the top *unmapped* source categories, which is
what you grow `config/taxonomy.yaml` aliases against.

Usage:
    python scripts/inspect_awin_feed.py <feed-url-or-path> [--limit N] [--publisher-id ID]

The P1-T3 definition-of-done is a mapping rate >70%; if it's lower, the unmapped
list below tells you which aliases to add.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

# Allow running as `python scripts/inspect_awin_feed.py` without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.catalog.sources.awin import AwinSource  # noqa: E402
from app.core.config import settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("feed", help="Awin feed URL or local CSV path (.csv or .csv.gz)")
    parser.add_argument(
        "--limit",
        type=int,
        default=2000,
        help="max rows to inspect (default: 2000; use 0 for the whole feed)",
    )
    parser.add_argument(
        "--publisher-id",
        default=settings.awin_publisher_id or "PUBLISHER_ID",
        help="Awin publisher id (defaults to AWIN_PUBLISHER_ID from env)",
    )
    args = parser.parse_args()

    source = AwinSource(args.feed, publisher_id=args.publisher_id)

    total = 0
    mapped = 0
    mapped_categories: Counter[str] = Counter()
    unmapped_sources: Counter[str] = Counter()
    preview: list = []

    for product in source.list_products():
        total += 1
        if product.is_mapped:
            mapped += 1
            mapped_categories[product.category] += 1  # type: ignore[index]
        else:
            unmapped_sources[product.source_category or "<empty>"] += 1
        if len(preview) < 5:
            preview.append(product)
        if args.limit and total >= args.limit:
            break

    print(f"\n=== First {len(preview)} normalized products ===")
    for product in preview:
        flag = product.category if product.is_mapped else f"UNMAPPED ({product.source_category})"
        print(f"  [{flag}] {product.title[:60]!r}")
        print(f"      price={product.price} {product.currency}  brand={product.brand}")
        print(f"      affiliate_url={product.affiliate_url[:90]}")

    rate = (mapped / total * 100) if total else 0.0
    print(f"\n=== Mapping rate ===\n  {mapped}/{total} rows mapped ({rate:.1f}%)")
    if total and rate < 70:
        print("  WARNING: below the P1-T3 threshold of 70% — expand taxonomy.yaml aliases.")

    print("\n=== Mapped category distribution ===")
    for category, count in mapped_categories.most_common():
        print(f"  {count:5d}  {category}")

    print("\n=== Top unmapped source categories (taxonomy.yaml candidates) ===")
    for source_category, count in unmapped_sources.most_common(15):
        print(f"  {count:5d}  {source_category}")


if __name__ == "__main__":
    main()
