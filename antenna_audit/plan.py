"""Turn a site inventory into a concrete, coordinate-level sheet plan.

Planning is kept separate from writing so that the Excel writer and the PNG
previewer consume exactly the same structure.  A preview that looks right is
therefore evidence that the workbook is right, not a second implementation that
might drift from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import layout
from .imaging import ImagePreparer
from .catalog import (
    ELECTRICAL_TILT_CATEGORIES,
    MECHANICAL_AZIMUTH_CATEGORIES,
    Photo,
    SiteInventory,
    heading_for,
)


@dataclass
class SlotPlan:
    """One photo box, or one empty placeholder, at a fixed sheet position."""

    box: layout.Box
    photo: Photo | None      # None for Post drop boxes and missing Pre photos
    kind: str                # "pre" | "post"
    placeholder_text: str = ""


@dataclass
class CategoryPlan:
    """A heading plus the boxes underneath it, on both the Pre and Post sides."""

    category: str
    heading: str
    heading_row: int
    heading_col: int
    heading_post_col: int
    slots: list[SlotPlan] = field(default_factory=list)


@dataclass
class ZonePlan:
    """One half of a sector: the Pre and Post columns for a group of categories."""

    title: str
    pre_col0: int
    post_col0: int
    categories: list[CategoryPlan] = field(default_factory=list)
    end_row: int = 0


@dataclass
class SectorPlan:
    """A full sector band: banner, zone headers, and the two zones."""

    number: int
    banner_row: int
    zone_header_row: int
    prepost_header_row: int
    left: ZonePlan
    right: ZonePlan
    end_row: int = 0


@dataclass
class SheetPlan:
    """Everything needed to render one site's workbook."""

    site: str
    title_row: int
    subtitle_row: int
    sectors: list[SectorPlan] = field(default_factory=list)
    total_rows: int = 0
    placed_photos: int = 0
    missing_slots: list[str] = field(default_factory=list)


def _box_rows_for(photo: Photo, preparer: ImagePreparer | None) -> int:
    """Row count for the box holding ``photo``, from its aspect ratio."""
    if preparer is None:
        return layout.DEFAULT_BOX_H_PX // layout.ROW_PX
    prepared = preparer.prepare(photo.path)
    if prepared is None:
        return layout.DEFAULT_BOX_H_PX // layout.ROW_PX
    return layout.rows_for_photo(prepared.width, prepared.height)


def _plan_zone(
    title: str,
    pre_col0: int,
    post_col0: int,
    categories: list[str],
    sector: int,
    inventory: SiteInventory,
    start_row: int,
    missing: list[str],
    preparer: ImagePreparer | None,
) -> tuple[ZonePlan, int]:
    zone = ZonePlan(title=title, pre_col0=pre_col0, post_col0=post_col0)
    row = start_row

    for category in categories:
        heading = heading_for(category, sector)
        photos = inventory.get(sector, category)
        plan = CategoryPlan(
            category=category,
            heading=heading,
            heading_row=row,
            heading_col=pre_col0,
            heading_post_col=post_col0,
        )
        box_row = row + layout.HEADING_ROWS + layout.HEADING_GAP_ROWS

        # Size every box first: the Post drop box mirrors the height of the Pre
        # photo facing it, so the two columns stay aligned all the way down.
        box_heights = [_box_rows_for(photo, preparer) for photo in photos]
        if not box_heights:
            box_heights = [layout.DEFAULT_BOX_H_PX // layout.ROW_PX]

        top = box_row
        for index, height in enumerate(box_heights):
            photo = photos[index] if index < len(photos) else None
            if photo is None:
                missing.append(f"Sector {sector} — {heading}")
            plan.slots.append(
                SlotPlan(
                    layout.Box(pre_col0, top, rows=height),
                    photo,
                    "pre",
                    "" if photo else "No Pre photo",
                )
            )
            plan.slots.append(
                SlotPlan(
                    layout.Box(post_col0, top, rows=height),
                    None,
                    "post",
                    "Paste Post photo here",
                )
            )
            top += height + layout.PHOTO_GAP_ROWS

        zone.categories.append(plan)
        row += layout.category_block_rows(box_heights)

    zone.end_row = row
    return zone, row


def build_plan(
    inventory: SiteInventory, preparer: ImagePreparer | None = None
) -> SheetPlan:
    """Compute the full sheet plan for one site.

    ``preparer`` is used only to read each photo's pixel size, so that boxes can
    be made just tall enough for the photo they hold.  Its results are cached,
    so measuring here costs nothing when the photos are embedded later.
    """
    plan = SheetPlan(site=inventory.site, title_row=1, subtitle_row=2)
    row = layout.TITLE_ROWS + 1

    for sector in inventory.sectors:
        banner_row = row
        zone_header_row = banner_row + layout.BANNER_ROWS
        prepost_header_row = zone_header_row + layout.ZONE_HEADER_ROWS
        body_row = banner_row + layout.SECTOR_BODY_OFFSET

        left, left_end = _plan_zone(
            "Electrical Tilt",
            layout.LEFT_PRE_COL0,
            layout.LEFT_POST_COL0,
            ELECTRICAL_TILT_CATEGORIES,
            sector,
            inventory,
            body_row,
            plan.missing_slots,
            preparer,
        )
        right, right_end = _plan_zone(
            "Mechanical Tilt & Azimuth",
            layout.RIGHT_PRE_COL0,
            layout.RIGHT_POST_COL0,
            MECHANICAL_AZIMUTH_CATEGORIES,
            sector,
            inventory,
            body_row,
            plan.missing_slots,
            preparer,
        )

        sector_plan = SectorPlan(
            number=sector,
            banner_row=banner_row,
            zone_header_row=zone_header_row,
            prepost_header_row=prepost_header_row,
            left=left,
            right=right,
        )
        sector_plan.end_row = max(left_end, right_end)
        plan.sectors.append(sector_plan)
        row = sector_plan.end_row + layout.SECTOR_GAP_ROWS

    plan.total_rows = row
    plan.placed_photos = sum(
        1
        for sector in plan.sectors
        for zone in (sector.left, sector.right)
        for category in zone.categories
        for slot in category.slots
        if slot.photo is not None
    )
    return plan
