"""Render a sheet plan to a PNG, so the layout can be checked without Excel.

The previewer walks the same :class:`SheetPlan` the workbook writer walks and
draws it at the same pixel coordinates.  If the preview looks right, the
workbook is laid out right — there is only one set of geometry.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import layout
from .imaging import ImagePreparer
from .plan import SheetPlan

SCALE = 0.5  # preview at half size; full sheets are several thousand pixels tall

INK = (31, 41, 51)
MUTED = (77, 87, 97)
FAINT = (154, 165, 177)
BANNER_BG = (31, 41, 51)
ZONE_BG = (228, 231, 235)
PRE_BG = (220, 235, 220)
POST_BG = (246, 231, 220)
PLACEHOLDER_BG = (250, 251, 252)
MISSING_BG = (253, 243, 243)
MISSING_INK = (176, 74, 74)
PAGE_BG = (255, 255, 255)

_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
_BOLD_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    for candidate in _BOLD_CANDIDATES if bold else _FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _column_x() -> dict[int, int]:
    """Left edge, in pixels, of every column the sheet uses."""
    widths = layout.column_widths()
    x, edges = 0, {}
    for col in range(0, layout.LAST_COL + 2):
        edges[col] = x
        x += widths.get(col, layout.BOX_COL_PX)
    return edges


def _row_y(plan: SheetPlan) -> dict[int, int]:
    """Top edge, in pixels, of every row — honouring the taller header rows.

    The workbook gives the banner and zone-header rows their own heights; the
    preview has to use the same ones or it stops being evidence about the sheet.
    """
    taller: dict[int, int] = {}
    for sector in plan.sectors:
        taller[sector.banner_row] = round(layout.BANNER_ROW_PT * 96 / 72)
        taller[sector.zone_header_row] = round(layout.ZONE_ROW_PT * 96 / 72)

    last = plan.total_rows + 6
    tops: dict[int, int] = {}
    y = 0
    for row in range(1, last + 1):
        tops[row] = y
        y += taller.get(row, layout.ROW_PX)
    tops[last + 1] = y
    return tops


def render_preview(
    plan: SheetPlan, out_path: Path, preparer: ImagePreparer,
    max_sectors: int | None = None,
) -> Path:
    """Draw ``plan`` to a PNG at ``out_path``."""
    edges = _column_x()
    tops = _row_y(plan)
    sectors = plan.sectors[:max_sectors] if max_sectors else plan.sectors
    last_row = sectors[-1].end_row if sectors else plan.total_rows

    width = edges[layout.LAST_COL + 1]
    height = tops.get(last_row + 3, (last_row + 3) * layout.ROW_PX)

    canvas = Image.new("RGB", (width, height), PAGE_BG)
    draw = ImageDraw.Draw(canvas)

    def y(row: int) -> int:
        return tops.get(row, (row - 1) * layout.ROW_PX)

    draw.text((edges[0], y(plan.title_row)),
              f"{plan.site} — Antenna Audit Photos", font=_font(22, True), fill=INK)
    draw.text((edges[0], y(plan.subtitle_row) + 4),
              "Pre photos placed automatically. Post photos pasted by hand.",
              font=_font(13), fill=MUTED)

    for sector in sectors:
        _draw_sector(draw, canvas, sector, edges, y, preparer)

    if SCALE != 1.0:
        canvas = canvas.resize(
            (max(1, int(width * SCALE)), max(1, int(height * SCALE))),
            Image.LANCZOS,
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG", optimize=True)
    return out_path


def _draw_sector(draw, canvas, sector, edges, y, preparer) -> None:
    banner_font = _font(15, True)
    zone_font = _font(12, True)
    heading_font = _font(12, True)
    note_font = _font(11)

    x0, x1 = edges[layout.MARGIN_LEFT], edges[layout.LAST_COL + 1]
    top = y(sector.banner_row)
    draw.rectangle([x0, top, x1, top + layout.ROW_PX + 6], fill=BANNER_BG)
    draw.text((x0 + 10, top + 4), f"Sector {sector.number}",
              font=banner_font, fill=(255, 255, 255))

    for zone in (sector.left, sector.right):
        zx0 = edges[zone.pre_col0]
        zx1 = edges[zone.post_col0 + layout.BOX_COLS]
        zy = y(sector.zone_header_row) + 6
        draw.rectangle([zx0, zy, zx1, zy + layout.ROW_PX], fill=ZONE_BG)
        draw.text((zx0 + 6, zy + 3), zone.title, font=zone_font, fill=INK)

        py = y(sector.prepost_header_row) + 6
        for col0, label, colour in (
            (zone.pre_col0, "Pre", PRE_BG),
            (zone.post_col0, "Post", POST_BG),
        ):
            bx0, bx1 = edges[col0], edges[col0 + layout.BOX_COLS]
            draw.rectangle([bx0, py, bx1, py + layout.ROW_PX], fill=colour)
            draw.text((bx0 + 6, py + 3), label, font=zone_font, fill=INK)

        for category in zone.categories:
            hy = y(category.heading_row)
            for col0 in (category.heading_col, category.heading_post_col):
                draw.text((edges[col0] + 2, hy + 3), category.heading,
                          font=heading_font, fill=MUTED)
            for slot in category.slots:
                _draw_slot(draw, canvas, slot, edges, y, preparer, note_font)


def _draw_slot(draw, canvas, slot, edges, y, preparer, note_font) -> None:
    box = slot.box
    bx0, by0 = edges[box.col0], y(box.row0)
    bx1, by1 = edges[box.col0 + box.cols], by0 + box.height_px

    if slot.photo is None:
        missing = slot.kind == "pre"
        draw.rectangle([bx0, by0, bx1, by1],
                       fill=MISSING_BG if missing else PLACEHOLDER_BG,
                       outline=MISSING_INK if missing else FAINT)
        draw.text(((bx0 + bx1) // 2 - 60, (by0 + by1) // 2),
                  slot.placeholder_text, font=note_font,
                  fill=MISSING_INK if missing else FAINT)
        return

    prepared = preparer.prepare(slot.photo.path)
    if prepared is None:
        draw.rectangle([bx0, by0, bx1, by1], fill=MISSING_BG, outline=MISSING_INK)
        return

    w, h = layout.fit_within(prepared.width, prepared.height,
                             box.width_px, box.height_px)
    dx, dy = box.centre_offsets(w, h)
    try:
        with Image.open(prepared.path) as img:
            thumb = img.convert("RGB").resize((max(1, w), max(1, h)), Image.LANCZOS)
            canvas.paste(thumb, (bx0 + dx, by0 + dy))
    except Exception:  # noqa: BLE001 - a preview must never fail the run
        draw.rectangle([bx0, by0, bx1, by1], fill=MISSING_BG, outline=MISSING_INK)
        return
    draw.rectangle([bx0 + dx, by0 + dy, bx0 + dx + w, by0 + dy + h], outline=FAINT)
