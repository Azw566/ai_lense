"""CLI entry point for an Awin ingestion run (roadmap P1-T6).

Wraps `app.catalog.ingest.run_ingestion` with the wiring an operator needs from
the command line: pick a feed, build the `AwinSource` + Qdrant client + DB
session, hand them off, exit non-zero on hard failure.

Usage:
    python scripts/ingest_awin.py --feed https://feeds.awin.com/.../products.csv.gz \\
        [--publisher-id 999999] [--since 2024-03-01] [--batch-size 64] [--limit 100]

The publisher id defaults to `settings.awin_publisher_id` so production runs
don't have to pass it. `--limit` is mostly for local smoke runs against a
sample feed; production ingests the whole snapshot.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

from app.catalog.ingest import DEFAULT_BATCH_SIZE, run_ingestion
from app.catalog.sources.awin import AwinSource
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.postgres import AsyncSessionLocal
from scripts.init_qdrant import _client as qdrant_client

logger = get_logger(__name__)


def _parse_since(raw: str) -> datetime:
    # Accept either a bare date or an ISO datetime — the Awin feed only carries
    # day-precision `last_updated` anyway.
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"unrecognised --since value: {raw!r}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed", required=True, help="Awin CSV feed URL or local path")
    parser.add_argument(
        "--publisher-id",
        default=settings.awin_publisher_id,
        help="Awin awinaffid (defaults to AWIN_PUBLISHER_ID env var)",
    )
    parser.add_argument(
        "--since",
        type=_parse_since,
        default=None,
        help="Skip products whose last_updated is before this date",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Products per batch (download + embed + upsert)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after N products (smoke runs only)",
    )
    return parser.parse_args(argv)


async def _async_main(args: argparse.Namespace) -> int:
    if not args.publisher_id:
        logger.error("ingest.config.missing", field="awin_publisher_id")
        return 2

    source = AwinSource(feed=args.feed, publisher_id=args.publisher_id)
    qdrant = qdrant_client()
    try:
        async with AsyncSessionLocal() as session:
            stats = await run_ingestion(
                session,
                qdrant,
                source,
                since=args.since,
                batch_size=args.batch_size,
                limit=args.limit,
            )
        logger.info(
            "ingest.done",
            retailer=source.retailer,
            products_in=stats.products_in,
            products_out=stats.products_out,
            unmapped=stats.unmapped,
            embed_failures=stats.embed_failures,
        )
        return 0
    finally:
        await qdrant.close()


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)
    try:
        return asyncio.run(_async_main(args))
    except Exception:  # noqa: BLE001
        logger.exception("ingest.failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
