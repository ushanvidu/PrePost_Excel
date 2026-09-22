"""Turn a site inventory into a concrete, coordinate-level sheet plan.

Planning is kept separate from writing so that the Excel writer and the PNG
previewer consume exactly the same structure.  A preview that looks right is
therefore evidence that the workbook is right, not a second implementation that
might drift from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pathlib import Path

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
    photo: Photo | None      # None for drop boxes and missing Pre photos
    kind: str                # "pre" | "before" | "post"
    placeholder_text: str = ""


@dataclass
class CategoryPlan:
    """A heading plus the boxes underneath it, repeated over every band."""

    category: str
    heading: str
    heading_row: int
    heading_cols: tuple[int, ...]
    slots: list[SlotPlan] = field(default_factory=list)

    @property
    def heading_col(self) -> int:
        """Where the leftmost (Pre) copy of the heading starts."""
        return self.heading_cols[0]


@dataclass
class ZonePlan:
    """One half of a sector: the photo columns for a group of categories."""

    title: str
    kinds: tuple[str, ...]
    band_cols: tuple[int, ...]
    categories: list[CategoryPlan] = field(default_factory=list)
    end_row: int = 0

    def col0_for(self, kind: str) -> int:
        """Where the band holding slots of this kind starts."""
        return self.band_cols[self.kinds.index(kind)]

    @property
    def pre_col0(self) -> int:
        return self.col0_for("pre")

    @property
    def post_col0(self) -> int:
        return self.col0_for("post")

    @property
    def before_col0(self) -> int:
        """Only the Manual template has this band."""
        return self.col0_for("before")


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
    sheet_layout: layout.SheetLayout = layout.DEFAULT_TEMPLATE
    sectors: list[SectorPlan] = field(default_factory=list)
    total_rows: int = 0
    placed_photos: int = 0
    placed_post_photos: int = 0
    missing_slots: list[str] = field(default_factory=list)


def _box_rows_for(photo: Photo, preparer: ImagePreparer | None) -> int:
    """Row count for the box holding ``photo``, from its aspect ratio."""
    if preparer is None:
        return layout.DEFAULT_BOX_H_PX // layout.ROW_PX
    prepared = preparer.prepare(photo.path)
    if prepared is None:
        return layout.DEFAULT_BOX_H_PX // layout.ROW_PX
    return layout.rows_for_photo(prepared.width, prepared.height)


# What a band holds when nothing is placed in it automatically.
DROP_BOX_TEXT = {
    "before": "Paste Before Swap photo here",
    "post": "Paste Post photo here",
}


def _plan_zone(
    title: str,
    side: str,
    sheet_layout: layout.SheetLayout,
    categories: list[str],
    sector: int,
    inventory: SiteInventory,
    start_row: int,
    missing: list[str],
    preparer: ImagePreparer | None,
    post_photos: dict[tuple[int, str], list[Path]] | None = None,
) -> tuple[ZonePlan, int]:
    band_cols = sheet_layout.bands(side)
    zone = ZonePlan(
        title=title, kinds=sheet_layout.kinds, band_cols=band_cols,
    )
    row = start_row

    for category in categories:
        heading = heading_for(category, sector)
        photos = inventory.get(sector, category)
        plan = CategoryPlan(
            category=category,
            heading=heading,
            heading_row=row,
            heading_cols=band_cols,
        )
        box_row = row + layout.HEADING_ROWS + layout.HEADING_GAP_ROWS

        # Size every box first: each drop box mirrors the height of the Pre
        # photo facing it, so the bands stay aligned all the way down.
        box_heights = [_box_rows_for(photo, preparer) for photo in photos]
        if not box_heights:
            box_heights = [layout.DEFAULT_BOX_H_PX // layout.ROW_PX]

        # Post photos, when the classifier has placed any for this slot.
        matched_post = (post_photos or {}).get((sector, category), [])

        top = box_row
        for index, height in enumerate(box_heights):
            photo = photos[index] if index < len(photos) else None
            if photo is None:
                missing.append(f"Sector {sector} — {heading}")
            # Post photos arrive as bare paths from the classifier; wrap them so
            # every slot carries the same shape and the writer needs no special
            # case.  The Before Swap band is never filled automatically —
            # nothing in the survey documents that round.
            post_path = matched_post[index] if index < len(matched_post) else None
            post = (
                Photo(path=post_path, sector=sector, category=category,
                      sequence=index + 1)
                if post_path is not None else None
            )
            placed = {"pre": photo, "post": post}

            for kind, col0 in zip(sheet_layout.kinds, band_cols):
                found = placed.get(kind)
                plan.slots.append(
                    SlotPlan(
                        layout.Box(col0, top, rows=height),
                        found,
                        kind,
                        "" if found else (
                            DROP_BOX_TEXT.get(kind, "No Pre photo")
                        ),
                    )
                )
            top += height + layout.PHOTO_GAP_ROWS

        zone.categories.append(plan)
        row += layout.category_block_rows(box_heights)

    zone.end_row = row
    return zone, row


def build_plan(
    inventory: SiteInventory,
    preparer: ImagePreparer | None = None,
    post_photos: dict[tuple[int, str], list[Path]] | None = None,
    sheet_layout: layout.SheetLayout | None = None,
) -> SheetPlan:
    """Compute the full sheet plan for one site.

    ``preparer`` is used only to read each photo's pixel size, so that boxes can
    be made just tall enough for the photo they hold.  Its results are cached,
    so measuring here costs nothing when the photos are embedded later.

    ``post_photos`` maps ``(sector, survey category)`` to the Post photographs
    the classifier resolved for that slot.  Anything absent keeps its empty drop
    box, so a slot the classifier could not resolve still reads as "fill this in
    by hand" rather than silently carrying a wrong photo.
    """
    sheet_layout = sheet_layout or layout.DEFAULT_TEMPLATE
    plan = SheetPlan(
        site=inventory.site, title_row=1, subtitle_row=2,
        sheet_layout=sheet_layout,
    )
    row = layout.TITLE_ROWS + 1

    for sector in inventory.sectors:
        banner_row = row
        zone_header_row = banner_row + layout.BANNER_ROWS
        prepost_header_row = zone_header_row + layout.ZONE_HEADER_ROWS
        body_row = banner_row + layout.SECTOR_BODY_OFFSET

        left, left_end = _plan_zone(
            "Electrical Tilt",
            "left",
            sheet_layout,
            ELECTRICAL_TILT_CATEGORIES,
            sector,
            inventory,
            body_row,
            plan.missing_slots,
            preparer,
            post_photos,
        )
        right, right_end = _plan_zone(
            "Mechanical Tilt & Azimuth",
            "right",
            sheet_layout,
            MECHANICAL_AZIMUTH_CATEGORIES,
            sector,
            inventory,
            body_row,
            plan.missing_slots,
            preparer,
            post_photos,
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
        if slot.photo is not None and slot.kind == "pre"
    )
    plan.placed_post_photos = sum(
        1
        for sector in plan.sectors
        for zone in (sector.left, sector.right)
        for category in zone.categories
        for slot in category.slots
        if slot.photo is not None and slot.kind == "post"
    )
    return plan
