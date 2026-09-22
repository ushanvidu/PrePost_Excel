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


@pytest.mark.parametrize("sheet_layout", [layout.MANUAL, layout.AR],
                         ids=lambda t: t.name)
def test_nothing_overlaps_in_a_complete_site(sheet_layout):
    plan = build_plan(make_inventory({1: full_sector(), 2: full_sector()}),
                      sheet_layout=sheet_layout)
    assert check_plan(plan) == []


def test_the_ar_template_has_no_before_swap_slot():
    plan = build_plan(make_inventory({1: full_sector()}), sheet_layout=layout.AR)
    kinds = {
        slot.kind
        for sector in plan.sectors
        for zone in (sector.left, sector.right)
        for category in zone.categories
        for slot in category.slots
    }
    assert kinds == {"pre", "post"}


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


@pytest.mark.parametrize("sheet_layout", [layout.MANUAL, layout.AR],
                         ids=lambda t: t.name)
def test_every_band_is_aligned_and_the_same_height(sheet_layout):
    plan = build_plan(make_inventory({1: full_sector(), 2: {c: 3 for c in
                                      ELECTRICAL_TILT_CATEGORIES}}),
                      sheet_layout=sheet_layout)
    for sector in plan.sectors:
        for zone in (sector.left, sector.right):
            assert zone.kinds == sheet_layout.kinds
            for category in zone.categories:
                rows = [
                    [s for s in category.slots if s.kind == kind]
                    for kind in sheet_layout.kinds
                ]
                assert len({len(band) for band in rows}) == 1, "bands differ in length"
                for slots in zip(*rows):
                    pre = slots[0]
                    for slot, kind in zip(slots, sheet_layout.kinds):
                        assert slot.box.row0 == pre.box.row0
                        assert slot.box.rows == pre.box.rows
                        assert slot.box.col0 == zone.col0_for(kind)


@pytest.mark.parametrize("sheet_layout", [layout.MANUAL, layout.AR],
                         ids=lambda t: t.name)
def test_drop_box_bands_never_hold_a_photo(sheet_layout):
    plan = build_plan(make_inventory({1: full_sector()}),
                      sheet_layout=sheet_layout)
    for sector in plan.sectors:
        for zone in (sector.left, sector.right):
            for category in zone.categories:
                for slot in category.slots:
                    if slot.kind in ("before", "post"):
                        assert slot.photo is None


def test_before_swap_is_never_filled_even_when_post_photos_are_supplied():
    """The survey has no Before Swap round, so that band stays a drop box."""
    plan = build_plan(
        make_inventory({1: full_sector()}),
        post_photos={(1, "850_Tilt"): [Path("/post/a.jpg")]},
    )
    category = next(c for c in plan.sectors[0].left.categories
                    if c.category == "850_Tilt")
    by_kind = {s.kind: s for s in category.slots}
    assert by_kind["post"].photo is not None, "the Post band should take it"
    assert by_kind["before"].photo is None


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
