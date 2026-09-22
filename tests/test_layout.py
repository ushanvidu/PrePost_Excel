"""Geometry: bands must not collide and boxes must not clip their contents."""

import pytest

from antenna_audit import layout


ALL_TEMPLATES = [layout.MANUAL, layout.AR]


@pytest.mark.parametrize("sheet_layout", ALL_TEMPLATES, ids=lambda t: t.name)
def test_column_bands_do_not_overlap(sheet_layout):
    bands = [
        (col0, f"{side} {kind}")
        for side in ("left", "right")
        for kind, col0 in zip(sheet_layout.kinds, sheet_layout.bands(side))
    ]
    for (start, name), (next_start, next_name) in zip(bands, bands[1:]):
        end = start + layout.BOX_COLS
        assert end <= next_start, f"{name} runs into {next_name}"
    last = bands[-1][0] + layout.BOX_COLS
    assert last <= sheet_layout.last_col + 1, "a band runs past the merged banner"


@pytest.mark.parametrize("sheet_layout", ALL_TEMPLATES, ids=lambda t: t.name)
def test_every_band_is_preceded_by_a_gap_column(sheet_layout):
    """A gap keeps two bands from reading as one wide block."""
    widths = sheet_layout.column_widths()
    first = sheet_layout.bands("left")[0]
    for side in ("left", "right"):
        for col0 in sheet_layout.bands(side):
            if col0 == first:
                continue                      # the left margin precedes this one
            assert widths[col0 - 1] < layout.BOX_COL_PX, f"no gap before {col0}"


@pytest.mark.parametrize("sheet_layout", ALL_TEMPLATES, ids=lambda t: t.name)
def test_every_used_column_has_a_width(sheet_layout):
    widths = sheet_layout.column_widths()
    for col in range(layout.MARGIN_LEFT, sheet_layout.last_col + 1):
        assert col in widths, f"column {col} has no explicit width"


@pytest.mark.parametrize("sheet_layout", ALL_TEMPLATES, ids=lambda t: t.name)
def test_the_sheet_is_exactly_as_wide_as_its_bands_need(sheet_layout):
    """Margins and gaps are the only columns that are not part of a band."""
    band_cols = {
        col0 + offset
        for side in ("left", "right")
        for col0 in sheet_layout.bands(side)
        for offset in range(layout.BOX_COLS)
    }
    spacers = set(range(0, sheet_layout.last_col + 1)) - band_cols
    assert len(band_cols) == len(sheet_layout.kinds) * 2 * layout.BOX_COLS
    # Two margins, one gutter, and one gap after every band but the last of each
    # zone (whose slot the gutter and the right margin take).
    assert len(spacers) == 2 + 1 + (len(sheet_layout.kinds) - 1) * 2


def test_ar_keeps_the_geometry_it_had_before_before_swap_existed():
    """Workbooks already in the field were built to exactly these columns."""
    assert layout.AR.left == (1, 9)
    assert layout.AR.right == (17, 25)
    assert layout.AR.gutter_col == 16
    assert layout.AR.last_col == 32


def test_only_manual_has_a_before_swap_band():
    assert layout.MANUAL.has_before_swap
    assert not layout.AR.has_before_swap
    assert layout.AR.kinds == ("pre", "post")


def test_templates_are_looked_up_by_name():
    assert layout.template("ar") is layout.AR
    assert layout.template("MANUAL") is layout.MANUAL
    assert layout.template(None) is layout.DEFAULT_TEMPLATE
    with pytest.raises(ValueError):
        layout.template("nope")


def test_box_width_matches_its_columns():
    box = layout.Box(layout.MANUAL.col0("left", "pre"), 10)
    assert box.width_px == layout.BOX_COLS * layout.BOX_COL_PX


def test_fit_never_exceeds_the_box():
    box_w, box_h = layout.BOX_W_PX, layout.MAX_BOX_H_PX
    for size in [(4000, 3000), (3000, 4000), (8000, 1000), (100, 9000), (50, 50)]:
        w, h = layout.fit_within(*size, box_w, box_h)
        assert w <= box_w and h <= box_h, f"{size} overflowed the box"


def test_fit_preserves_aspect_ratio():
    w, h = layout.fit_within(4032, 3024, layout.BOX_W_PX, layout.MAX_BOX_H_PX)
    assert abs((w / h) - (4032 / 3024)) < 0.01


def test_small_images_are_not_upscaled():
    w, h = layout.fit_within(120, 90, layout.BOX_W_PX, layout.MAX_BOX_H_PX)
    assert (w, h) == (120, 90)


def test_photo_always_fits_the_rows_reserved_for_it():
    """The clamped row count must never be shorter than the fitted photo."""
    for size in [(4000, 3000), (3000, 4000), (9000, 900), (900, 9000), (1, 1)]:
        rows = layout.rows_for_photo(*size)
        _, height = layout.fit_within(*size, layout.BOX_W_PX, layout.MAX_BOX_H_PX)
        assert rows * layout.ROW_PX >= height, f"{size} would be clipped"


def test_block_rows_account_for_every_box():
    heights = [30, 21, 14]
    total = layout.category_block_rows(heights)
    consumed = (
        layout.HEADING_ROWS
        + layout.HEADING_GAP_ROWS
        + sum(heights)
        + (len(heights) - 1) * layout.PHOTO_GAP_ROWS
    )
    assert total == consumed + layout.CATEGORY_GAP_ROWS
