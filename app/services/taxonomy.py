"""Category taxonomy: loads config/taxonomy.yaml and maps source categories to ours.

The taxonomy is versioned config (arch §5.3), not code — this module only reads it.
Public surface:
  - `Taxonomy.from_yaml(path)` — load and validate a taxonomy file.
  - `taxonomy.map_source_category(retailer, source_cat)` — source string -> our
    category, or None when nothing matches (caller quarantines to unmapped_products).
  - `taxonomy.yolo_class_for(category)` — COCO class id for a category, or None.
  - module-level `map_source_category(...)` — convenience over the default instance.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_TAXONOMY_PATH = Path(__file__).resolve().parents[2] / "config" / "taxonomy.yaml"

# Breadcrumb separators seen across Awin merchant feeds ("Home > Furniture > Sofas").
_BREADCRUMB_SPLIT = re.compile(r"[>/|»]")
_WHITESPACE = re.compile(r"\s+")


def _normalize(raw: str) -> str:
    """Lowercase, strip surrounding quotes/whitespace, collapse inner whitespace."""
    return _WHITESPACE.sub(" ", raw.strip().strip("\"'").lower()).strip()


class TaxonomyError(ValueError):
    """Raised when taxonomy.yaml is structurally invalid or has ambiguous aliases."""


class Taxonomy:
    """In-memory view of taxonomy.yaml with fast source-category lookup."""

    def __init__(
        self,
        version: int,
        yolo_classes: dict[str, int | None],
        # {retailer: {normalized_alias: canonical_category}}
        alias_index: dict[str, dict[str, str]],
    ) -> None:
        self.version = version
        self._yolo_classes = yolo_classes
        self._alias_index = alias_index

    @classmethod
    def from_yaml(cls, path: Path | str = DEFAULT_TAXONOMY_PATH) -> Taxonomy:
        path = Path(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "your_categories" not in data:
            raise TaxonomyError(f"{path}: missing top-level 'your_categories' key")

        version = int(data.get("version", 1))
        categories = data["your_categories"]
        if not isinstance(categories, dict) or not categories:
            raise TaxonomyError(f"{path}: 'your_categories' must be a non-empty mapping")

        yolo_classes: dict[str, int | None] = {}
        alias_index: dict[str, dict[str, str]] = {}

        for category, entry in categories.items():
            if not isinstance(entry, dict):
                raise TaxonomyError(f"{path}: category '{category}' must be a mapping")
            yolo_classes[category] = entry.get("yolo_class")

            aliases = (entry.get("aliases") or {})
            for retailer, source_cats in aliases.items():
                if not isinstance(source_cats, list):
                    raise TaxonomyError(
                        f"{path}: aliases.{retailer} for '{category}' must be a list"
                    )
                retailer_index = alias_index.setdefault(retailer, {})
                for source_cat in source_cats:
                    key = _normalize(str(source_cat))
                    existing = retailer_index.get(key)
                    if existing is not None and existing != category:
                        # Ambiguous mapping is a config bug — fail loudly at load,
                        # not silently mis-bucket every matching product later.
                        raise TaxonomyError(
                            f"{path}: alias '{source_cat}' for retailer '{retailer}' "
                            f"maps to both '{existing}' and '{category}'"
                        )
                    retailer_index[key] = category

        logger.info(
            "taxonomy.loaded",
            path=str(path),
            version=version,
            categories=len(categories),
            retailers=sorted(alias_index),
        )
        return cls(version, yolo_classes, alias_index)

    def map_source_category(self, retailer: str, source_cat: str | None) -> str | None:
        """Map a retailer's source category string to one of our canonical categories.

        Tries the whole normalized string first, then the last breadcrumb segment
        (so both "Sofas" and "Home > Furniture > Sofas" resolve). Returns None when
        nothing matches — the caller is expected to quarantine the product.
        """
        if not source_cat:
            return None
        retailer_index = self._alias_index.get(retailer)
        if not retailer_index:
            return None

        candidates = [_normalize(source_cat)]
        segments = [s for s in _BREADCRUMB_SPLIT.split(source_cat) if s.strip()]
        if len(segments) > 1:
            candidates.append(_normalize(segments[-1]))

        for candidate in candidates:
            match = retailer_index.get(candidate)
            if match is not None:
                return match
        return None

    def yolo_class_for(self, category: str) -> int | None:
        """COCO class id for a canonical category, or None if it has no COCO match."""
        return self._yolo_classes.get(category)

    @property
    def categories(self) -> list[str]:
        return list(self._yolo_classes)


# Default instance, loaded once from the repo's config/taxonomy.yaml.
default_taxonomy = Taxonomy.from_yaml()


def map_source_category(retailer: str, source_cat: str | None) -> str | None:
    """Convenience wrapper over the default taxonomy instance."""
    return default_taxonomy.map_source_category(retailer, source_cat)
