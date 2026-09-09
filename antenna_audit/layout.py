"""The sheet's geometry, expressed once so the writer and the previewer agree.

All measurements are in CSS pixels at 96 dpi, which is the unit Excel's drawing
layer effectively works in (1 px = 9525 EMU).  Rows and columns are given fixed
sizes by the writer so that a position computed here lands where it is expected
no matter what the reader's default font is.

Column bands, left to right::

    A          margin
    B .. H     electrical tilt, Pre           <- filled from the survey photos
    I          gap
    J .. P     electrical tilt, Post          <- empty drop boxes, filled by hand
    Q          centre gutter
    R .. X     mechanical tilt / azimuth, Pre
    Y          gap
    Z .. AF    mechanical tilt / azimuth, Post
    AG         margin

Box heights adapt to each photo's aspect ratio.  A landscape tilt shot would
otherwise sit in the middle of a tall portrait-shaped box with most of the slot
left empty, which pushes the sheet to several times the length it needs.  The
Pre box and its facing Post drop box always share a height, so the two columns
stay aligned row for row.
"""

from __future__ import annotations

from dataclasses import dataclass

EMU_PER_PX = 9525

# --- Cell metrics -----------------------------------------------------------
# Width of a column that holds part of an image box.
BOX_COL_PX = 64
# Narrow columns used for margins and gaps.
GAP_COL_PX = 20
GUTTER_COL_PX = 32
# Every content row is this tall (15 pt).
ROW_PX = 20

# Excel stores column width as a character count in units of MDW, the maximum
# digit width of the body font (7 px for Calibri 11).
#
# Readers disagree about whether the stored number already carries Excel's 5 px
# of cell padding: one reading renders a stored width w as w*MDW px, the other
# as w*MDW + 5.  We store px/MDW, which is what the reference workbook itself
# uses (defaultColWidth 9.142857 == 64/7).  Under the first reading a band comes
# out at exactly BOX_W_PX; under the second every column is 5 px wider, so the
# band is *larger* than the picture drawn in it.  A photo therefore stays inside
# its band under either reading, which matters because the picture's own size is
# pinned in EMU and does not follow the column width.
MAX_DIGIT_WIDTH = 7


def px_to_col_width(px: int) -> float:
    """Convert a pixel width to the character-count unit Excel stores."""
    return px / MAX_DIGIT_WIDTH


def px_to_row_height(px: int) -> float:
    """Convert a pixel height to points (Excel's row height unit)."""
    return px * 72 / 96


# --- Column bands -----------------------------------------------------------
# (first_column_index, column_count), zero-based.
MARGIN_LEFT = 0
LEFT_PRE_COL0, BOX_COLS = 1, 7
LEFT_GAP_COL = 8
LEFT_POST_COL0 = 9
GUTTER_COL = 16
RIGHT_PRE_COL0 = 17
RIGHT_GAP_COL = 24
RIGHT_POST_COL0 = 25
MARGIN_RIGHT = 32
LAST_COL = MARGIN_RIGHT  # banner rows are merged from column A to here

BOX_W_PX = BOX_COLS * BOX_COL_PX  # 448

# A box is never shorter than MIN nor taller than MAX, whatever the photo's
# shape.  MIN keeps a wide panorama from collapsing into a letterbox strip that
# is awkward to paste a Post photo into; MAX keeps a very tall photo from
# running off the screen.
MIN_BOX_H_PX = 260
MAX_BOX_H_PX = 620
# Height used for a slot with no Pre photo to measure.
DEFAULT_BOX_H_PX = 420
BOX_ROWS = MAX_BOX_H_PX // ROW_PX

# --- Vertical rhythm (in rows) ---------------------------------------------
TITLE_ROWS = 3          # workbook title, subtitle, spacer
BANNER_ROWS = 1         # "Sector N"
ZONE_HEADER_ROWS = 1    # "Electrical Tilt" / "Mechanical Tilt & Azimuth"
PREPOST_HEADER_ROWS = 1 # "Pre" / "Post"
HEADER_GAP_ROWS = 1     # breathing room before the first photo
HEADING_ROWS = 1        # the category heading, directly above its photos
HEADING_GAP_ROWS = 1
PHOTO_GAP_ROWS = 2      # between stacked photos of the same category
CATEGORY_GAP_ROWS = 3   # after a category block
SECTOR_GAP_ROWS = 3     # after a sector

SECTOR_BODY_OFFSET = (
    BANNER_ROWS + ZONE_HEADER_ROWS + PREPOST_HEADER_ROWS + HEADER_GAP_ROWS
)

# Row height in points for the special header rows.
BANNER_ROW_PT = 21.0
ZONE_ROW_PT = 18.0


def category_block_rows(box_heights: list[int]) -> int:
    """How many rows a heading plus its stack of photo boxes occupies.

    ``box_heights`` is one row count per box.  A category with no photos still
    gets one box, holding a "no photo" note, so that every site's sheet keeps
    the same shape.
    """
    heights = box_heights or [DEFAULT_BOX_H_PX // ROW_PX]
    return (
        HEADING_ROWS
        + HEADING_GAP_ROWS
        + sum(heights)
        + (len(heights) - 1) * PHOTO_GAP_ROWS
        + CATEGORY_GAP_ROWS
    )


def rows_for_photo(img_w: int, img_h: int) -> int:
    """How many rows a box holding this photo should occupy.

    The photo is fitted to the fixed box width, and the box is made just tall
    enough to hold the result, clamped into the min/max range.
    """
    if img_w <= 0 or img_h <= 0:
        return DEFAULT_BOX_H_PX // ROW_PX
    _, height = fit_within(img_w, img_h, BOX_W_PX, MAX_BOX_H_PX)
    clamped = min(max(height, MIN_BOX_H_PX), MAX_BOX_H_PX)
    return max(1, -(-clamped // ROW_PX))  # round up to a whole row


@dataclass(frozen=True)
class Box:
    """A rectangular region of cells that holds one photo (or one drop box)."""

    col0: int
    row0: int
    cols: int = BOX_COLS
    rows: int = BOX_ROWS

    @property
    def width_px(self) -> int:
        return self.cols * BOX_COL_PX

    @property
    def height_px(self) -> int:
        return self.rows * ROW_PX

    def centre_offsets(self, img_w: int, img_h: int) -> tuple[int, int]:
        """Pixel offsets that centre an image of the given size inside the box."""
        return (self.width_px - img_w) // 2, (self.height_px - img_h) // 2


def fit_within(img_w: int, img_h: int, box_w: int, box_h: int) -> tuple[int, int]:
    """Scale ``img_w`` x ``img_h`` down to fit the box, preserving aspect ratio.

    Images smaller than the box are left at their natural size rather than being
    blown up, which would only make them blurry.
    """
    if img_w <= 0 or img_h <= 0:
        return box_w, box_h
    scale = min(box_w / img_w, box_h / img_h, 1.0)
    return max(1, round(img_w * scale)), max(1, round(img_h * scale))


def column_widths() -> dict[int, int]:
    """Pixel width for every column the sheet uses, keyed by zero-based index."""
    widths: dict[int, int] = {MARGIN_LEFT: GAP_COL_PX}
    for col0 in (LEFT_PRE_COL0, LEFT_POST_COL0, RIGHT_PRE_COL0, RIGHT_POST_COL0):
        for offset in range(BOX_COLS):
            widths[col0 + offset] = BOX_COL_PX
    widths[LEFT_GAP_COL] = GAP_COL_PX
    widths[RIGHT_GAP_COL] = GAP_COL_PX
    widths[GUTTER_COL] = GUTTER_COL_PX
    widths[MARGIN_RIGHT] = GAP_COL_PX
    return widths
