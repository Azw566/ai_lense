"""Tests for the category taxonomy loader and source-category normalizer (P1-T2)."""

import textwrap
from pathlib import Path

import pytest

from app.services.taxonomy import Taxonomy, TaxonomyError, default_taxonomy

# 20 representative Awin source category strings -> expected canonical category.
# Mix of bare names, breadcrumb paths, casing and quoting variations.
AWIN_CASES = [
    ("Sofas", "couch"),
    ("Corner Sofas", "couch"),
    ("Home & Garden > Furniture > 3 Seater Sofas", "couch"),
    ("Armchairs", "armchair"),
    ("Accent Chairs", "armchair"),
    ("Dining Chairs", "chair"),
    ("Bar Stools", "chair"),
    ("Dining Tables", "dining_table"),
    ("Furniture / Tables / Coffee Tables", "coffee_table"),
    ("Bedside Tables", "side_table"),
    ("Beds", "bed"),
    ("Mattresses", "mattress"),
    ("Chests of Drawers", "dresser"),
    ("Bookcases & Shelving", "bookshelf"),
    ("FLOOR LAMPS", "lamp_floor"),
    ("Ceiling Lights", "lamp_ceiling"),
    ("Rugs", "rug"),
    ("Wall Art & Prints", "wall_art"),
    ("Plant Pots & Planters", "plant_pot"),
    ("  Cushions  ", "cushion"),
    ("Office Desks", "desk"),
    ("TV Units", "tv_unit"),
]

# Strings that must NOT resolve -> caller quarantines to unmapped_products.
UNMAPPED_CASES = [
    "Garden Furniture",
    "Kitchen Appliances",
    "",
    "Electronics > TVs",
]


@pytest.mark.parametrize(("source_cat", "expected"), AWIN_CASES)
def test_map_known_awin_categories(source_cat: str, expected: str) -> None:
    assert default_taxonomy.map_source_category("awin", source_cat) == expected


@pytest.mark.parametrize("source_cat", UNMAPPED_CASES)
def test_unmapped_categories_return_none(source_cat: str) -> None:
    assert default_taxonomy.map_source_category("awin", source_cat) is None


def test_unknown_retailer_returns_none() -> None:
    assert default_taxonomy.map_source_category("ikea", "Sofas") is None


def test_none_source_category_returns_none() -> None:
    assert default_taxonomy.map_source_category("awin", None) is None


def test_breadcrumb_last_segment_matching() -> None:
    # Full string is unknown but the last breadcrumb segment resolves.
    assert default_taxonomy.map_source_category("awin", "Whatever > Nonsense > Mirrors") == "mirror"


def test_yolo_class_lookup() -> None:
    assert default_taxonomy.yolo_class_for("couch") == 57
    assert default_taxonomy.yolo_class_for("clock") == 74
    # Soft furnishings have no COCO equivalent.
    assert default_taxonomy.yolo_class_for("rug") is None
    assert default_taxonomy.yolo_class_for("does_not_exist") is None


def test_default_taxonomy_has_expected_shape() -> None:
    assert default_taxonomy.version == 1
    # Roadmap P1-T2 targets 15-25; desk + tv_unit added per QA review push it to 27.
    assert 15 <= len(default_taxonomy.categories) <= 27
    assert "couch" in default_taxonomy.categories


def _write_taxonomy(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "taxonomy.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_ambiguous_alias_raises(tmp_path: Path) -> None:
    path = _write_taxonomy(
        tmp_path,
        """
        version: 1
        your_categories:
          couch:
            yolo_class: 57
            aliases:
              awin: ["Sofas"]
          armchair:
            yolo_class: 56
            aliases:
              awin: ["Sofas"]
        """,
    )
    with pytest.raises(TaxonomyError, match="maps to both"):
        Taxonomy.from_yaml(path)


def test_missing_top_level_key_raises(tmp_path: Path) -> None:
    path = _write_taxonomy(tmp_path, "version: 1\n")
    with pytest.raises(TaxonomyError, match="your_categories"):
        Taxonomy.from_yaml(path)
