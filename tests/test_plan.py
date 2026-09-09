"""Plan-level invariants, built from synthetic inventories."""

from pathlib import Path

import pytest

from antenna_audit import layout
from antenna_audit.catalog import (
    ELECTRICAL_TILT_CATEGORIES,
    MECHANICAL_AZIMUTH_CATEGORIES,
    Photo,
    SiteInventory,
)
from antenna_audit.plan import build_plan
from antenna_audit.validate import check_plan


def make_inventory(spec: dict[int, dict[str, int]]) -> SiteInventory:
    """Build an inventory from {sector: {category: photo_count}}."""
    photos = []
    for sector, categories in spec.items():
        for category, count in categories.items():
            for seq in range(1, count + 1):
                photos.append(
                    Photo(
                        path=Path(f"/photos/s{sector}_{category}_{seq}.jpg"),
                        sector=sector,
                        category=category,
                        sequence=seq,
                    )
                )
    return SiteInventory(site="TEST01", root=Path("/photos"), photos=photos)


def full_sector() -> dict[str, int]:
    return {c: 1 for c in ELECTRICAL_TILT_CATEGORIES + MECHANICAL_AZIMUTH_CATEGORIES}


def test_nothing_overlaps_in_a_complete_site():
    plan = build_plan(make_inventory({1: full_sector(), 2: full_sector()}))
    assert check_plan(plan) == []


def test_nothing_overlaps_when_categories_are_missing():
    sparse = {"1800_Tilt_1": 1, "Antenna_M_Tilt": 1}
    plan = build_plan(make_inventory({1: sparse, 2: sparse, 3: sparse}))
    assert check_plan(plan) == []


def test_nothing_overlaps_with_many_duplicates():
    heavy = {c: 4 for c in ELECTRICAL_TILT_CATEGORIES + MECHANICAL_AZIMUTH_CATEGORIES}
    plan = build_plan(make_inventory({1: heavy, 2: heavy}))
    assert check_plan(plan) == []


def test_missing_category_still_gets_a_slot():
    """Every site keeps the same shape, so sheets stay comparable."""
    plan = build_plan(make_inventory({1: {"850_Tilt": 1}}))
    headings = [c.heading for c in plan.sectors[0].left.categories]
    assert headings == [
        "Sec 1_ 850 Tilt", "Sec 1_ 900 Tilt", "Sec 1_ 1800 Tilt 1",
        "Sec 1_ 1800 Tilt 2", "Sec 1_ 2100 Tilt",
    ]
    assert len(plan.missing_slots) == 4 + len(MECHANICAL_AZIMUTH_CATEGORIES)


def test_pre_and_post_boxes_are_aligned_and_same_height():
    plan = build_plan(make_inventory({1: full_sector(), 2: {c: 3 for c in
                                      ELECTRICAL_TILT_CATEGORIES}}))
    for sector in plan.sectors:
        for zone in (sector.left, sector.right):
            for category in zone.categories:
                pres = [s for s in category.slots if s.kind == "pre"]
                posts = [s for s in category.slots if s.kind == "post"]
                assert len(pres) == len(posts)
                for pre, post in zip(pres, posts):
                    assert pre.box.row0 == post.box.row0
                    assert pre.box.rows == post.box.rows
                    assert pre.box.col0 == zone.pre_col0
                    assert post.box.col0 == zone.post_col0


def test_post_boxes_never_hold_a_photo():
    plan = build_plan(make_inventory({1: full_sector()}))
    for sector in plan.sectors:
        for zone in (sector.left, sector.right):
            for category in zone.categories:
                for slot in category.slots:
                    if slot.kind == "post":
                        assert slot.photo is None


def test_every_photo_sits_below_its_heading():
    plan = build_plan(make_inventory({1: {c: 2 for c in ELECTRICAL_TILT_CATEGORIES}}))
    for zone in (plan.sectors[0].left, plan.sectors[0].right):
        for category in zone.categories:
            for slot in category.slots:
                assert slot.box.row0 > category.heading_row


def test_sectors_do_not_run_into_each_other():
    plan = build_plan(make_inventory({1: full_sector(), 2: full_sector(),
                                      3: full_sector()}))
    for earlier, later in zip(plan.sectors, plan.sectors[1:]):
        assert earlier.end_row < later.banner_row


def test_left_and_right_zones_carry_the_agreed_categories():
    plan = build_plan(make_inventory({1: full_sector()}))
    sector = plan.sectors[0]
    assert [c.category for c in sector.left.categories] == ELECTRICAL_TILT_CATEGORIES
    assert [c.category for c in sector.right.categories] == MECHANICAL_AZIMUTH_CATEGORIES


def test_duplicate_photos_are_all_placed():
    plan = build_plan(make_inventory({1: {"2100_Tilt": 4}}))
    category = next(c for c in plan.sectors[0].left.categories
                    if c.category == "2100_Tilt")
    assert len([s for s in category.slots if s.kind == "pre" and s.photo]) == 4
