"""Geometry: bands must not collide and boxes must not clip their contents."""

from antenna_audit import layout


def test_column_bands_do_not_overlap():
    bands = [
        (layout.LEFT_PRE_COL0, "left pre"),
        (layout.LEFT_POST_COL0, "left post"),
        (layout.RIGHT_PRE_COL0, "right pre"),
        (layout.RIGHT_POST_COL0, "right post"),
    ]
    for (start, name), (next_start, next_name) in zip(bands, bands[1:]):
        end = start + layout.BOX_COLS
        assert end <= next_start, f"{name} runs into {next_name}"
    last = bands[-1][0] + layout.BOX_COLS
    assert last <= layout.LAST_COL + 1, "a band runs past the merged banner"


def test_every_used_column_has_a_width():
    widths = layout.column_widths()
    for col in range(layout.MARGIN_LEFT, layout.MARGIN_RIGHT + 1):
        assert col in widths, f"column {col} has no explicit width"


def test_box_width_matches_its_columns():
    box = layout.Box(layout.LEFT_PRE_COL0, 10)
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
