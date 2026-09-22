"""The sheet's geometry, expressed once so the writer and the previewer agree.

All measurements are in CSS pixels at 96 dpi, which is the unit Excel's drawing
layer effectively works in (1 px = 9525 EMU).  Rows and columns are given fixed
sizes by the writer so that a position computed here lands where it is expected
no matter what the reader's default font is.

There are two templates, chosen per site.  **Manual** carries three bands in
each half of the sheet — the survey photo, the state found immediately before
the antenna swap, and the state after it.  **AR** is the older shape and has no
Before Swap band.  Only Pre is ever filled automatically; the rest are drop
boxes a person pastes into.

Both are laid out by the same rule, so adding or removing a band moves
everything downstream of it without any column being typed out by hand: a
margin, then each zone's bands at a fixed stride of one band plus one gap
column, a wider gutter between the two zones, and a margin at the end.

Manual, left to right::

    A          margin
    B .. H     electrical tilt, Pre           <- filled from the survey photos
    I          gap
    J .. P     electrical tilt, Before Swap   <- empty drop boxes, filled by hand
    Q          gap
    R .. X     electrical tilt, Post          <- empty drop boxes, filled by hand
    Y          centre gutter
    Z .. AF    mechanical tilt / azimuth, Pre
    AG         gap
    AH .. AN   mechanical tilt / azimuth, Before Swap
    AO         gap
    AP .. AV   mechanical tilt / azimuth, Post
    AW         margin

AR, the same without the Before Swap band::

    A          margin
    B .. H     electrical tilt, Pre
    I          gap
    J .. P     electrical tilt, Post
    Q          centre gutter
    R .. X     mechanical tilt / azimuth, Pre
    Y          gap
    Z .. AF    mechanical tilt / azimuth, Post
    AG         margin

Box heights adapt to each photo's aspect ratio.  A landscape tilt shot would
otherwise sit in the middle of a tall portrait-shaped box with most of the slot
left empty, which pushes the sheet to several times the length it needs.  The
Pre box and the two drop boxes facing it always share a height, so the three
columns stay aligned row for row.
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
BOX_COLS = 7
# One band plus the gap column that follows it.
BAND_STRIDE = BOX_COLS + 1

# What each band is called wherever a person sees it: the sheet's own header
# row, the preview, the Values sheet, and the buttons in the browser.
BAND_LABELS = {"pre": "Pre", "before": "Before Swap", "post": "Post"}

BOX_W_PX = BOX_COLS * BOX_COL_PX  # 448


@dataclass(frozen=True)
class SheetLayout:
    """Where every column band sits, for one of the two templates.

    A slot carries the *kind* of band it belongs to ("pre", "before", "post")
    rather than a column number, and this turns one into the other.  Nothing
    downstream needs to know which template it is working on.
    """

    name: str                     # the value the CLI and the browser send
    label: str                    # what a person sees on the button
    kinds: tuple[str, ...]        # bands per zone, in sheet order
    left: tuple[int, ...]         # first column of each left-zone band
    right: tuple[int, ...]        # first column of each right-zone band
    gutter_col: int
    last_col: int                 # banner rows are merged from column A to here

    @property
    def has_before_swap(self) -> bool:
        return "before" in self.kinds

    @property
    def labels(self) -> tuple[str, ...]:
        """What each band is called, in sheet order."""
        return tuple(BAND_LABELS[kind] for kind in self.kinds)

    @property
    def filled_by_hand(self) -> tuple[str, ...]:
        """The bands nothing is placed into automatically."""
        return tuple(BAND_LABELS[k] for k in self.kinds if k != "pre")

    def bands(self, side: str) -> tuple[int, ...]:
        return self.left if side == "left" else self.right

    def col0(self, side: str, kind: str) -> int:
        """First column of one band, by side of the sheet and slot kind."""
        return self.bands(side)[self.kinds.index(kind)]

    def band_columns(self) -> dict[str, int]:
        """Every band's first column, mapped to the kind it holds.

        Used to read a finished workbook back: a photo is matched to the nearest
        band, and this says what that band means.
        """
        return {
            col0: kind
            for side in ("left", "right")
            for kind, col0 in zip(self.kinds, self.bands(side))
        }

    def column_widths(self) -> dict[int, int]:
        """Pixel width for every column the sheet uses, by zero-based index."""
        widths: dict[int, int] = {MARGIN_LEFT: GAP_COL_PX}
        for side in ("left", "right"):
            for col0 in self.bands(side):
                for offset in range(BOX_COLS):
                    widths[col0 + offset] = BOX_COL_PX
                # The gap column that follows this band, unless the gutter or
                # the right margin already occupies that slot.
                widths.setdefault(col0 + BOX_COLS, GAP_COL_PX)
        widths[self.gutter_col] = GUTTER_COL_PX
        widths[self.last_col] = GAP_COL_PX
        return widths


def _make_layout(name: str, label: str, kinds: tuple[str, ...]) -> SheetLayout:
    """Place ``len(kinds)`` bands per zone at a fixed stride, twice over."""
    left = tuple(
        MARGIN_LEFT + 1 + index * BAND_STRIDE for index in range(len(kinds))
    )
    gutter = left[-1] + BOX_COLS
    right = tuple(gutter + 1 + index * BAND_STRIDE for index in range(len(kinds)))
    return SheetLayout(
        name=name, label=label, kinds=kinds, left=left, right=right,
        gutter_col=gutter, last_col=right[-1] + BOX_COLS,
    )


MANUAL = _make_layout("manual", "Manual", ("pre", "before", "post"))
AR = _make_layout("ar", "AR", ("pre", "post"))
TEMPLATES = {template.name: template for template in (MANUAL, AR)}
DEFAULT_TEMPLATE = MANUAL


def template(name: str | None) -> SheetLayout:
    """Look up a template by name, falling back to the default for ``None``."""
    if not name:
        return DEFAULT_TEMPLATE
    try:
        return TEMPLATES[name.strip().lower()]
    except KeyError:
        raise ValueError(
            f"unknown template {name!r}; choose one of "
            f"{', '.join(sorted(TEMPLATES))}"
        ) from None

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
