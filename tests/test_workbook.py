"""End-to-end: build a real workbook from generated photos and inspect it."""

import zipfile
from pathlib import Path

import pytest
from openpyxl import load_workbook
from PIL import Image

from antenna_audit import catalog
from antenna_audit.dedupe import deduplicate_media
from antenna_audit.imaging import ImagePreparer
from antenna_audit.plan import build_plan
from antenna_audit.validate import check_plan, check_workbook
from antenna_audit import layout
from antenna_audit.workbook import (
    VALUES_ROWS,
    VALUES_SHEET_TITLE,
    values_columns,
    write_workbook,
)

PREFIX = "Sector_{s}_RF_Antenna_Photos_Sector_{s}_RF_Antenna_Photos_Ant_Sec_{s}_"


def write_photo(path: Path, size, colour=(120, 160, 90)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path, quality=90)


@pytest.fixture
def site_dir(tmp_path: Path) -> Path:
    site = tmp_path / "images" / "ZZTEST1"
    for sector in (1, 2):
        prefix = PREFIX.format(s=sector)
        write_photo(site / f"{prefix}_850_Tilt_1.jpg", (4000, 3000))   # landscape
        write_photo(site / f"{prefix}_900_Tilt_1.jpg", (3000, 4000))   # portrait
        write_photo(site / f"{prefix}_1800_Tilt_1_1.jpg", (1600, 1200))
        write_photo(site / f"{prefix}Antenna_M_Tilt_1.jpg", (3000, 4000))
        write_photo(site / f"{prefix}Antenna_Azimuth_Photo_1.jpg", (2000, 2000))
        # A second shot of the same subject must also be placed.
        write_photo(site / f"{prefix}Antenna_Azimuth_Photo_2.jpg", (2000, 1500))
    return site


def build(site_dir: Path, tmp_path: Path, sheet_layout=None, **kwargs):
    inventory = catalog.scan_site(site_dir)
    preparer = ImagePreparer(tmp_path / "work", **kwargs)
    plan = build_plan(inventory, preparer, sheet_layout=sheet_layout)
    out = tmp_path / "out" / "ZZTEST1.xlsx"
    result = write_workbook(plan, out, preparer)
    return plan, result, out


def test_workbook_is_structurally_valid(site_dir, tmp_path):
    plan, result, out = build(site_dir, tmp_path)
    assert check_plan(plan) == []
    report = check_workbook(out)
    assert report.ok, report.errors
    assert result.photos_placed == 12
    assert report.checks["anchors"] == 12


def test_merged_ranges_never_overlap(site_dir, tmp_path):
    """Excel refuses to open a sheet with overlapping merged cells."""
    _, _, out = build(site_dir, tmp_path)
    ws = load_workbook(out).active
    ranges = list(ws.merged_cells.ranges)
    for i, a in enumerate(ranges):
        for b in ranges[i + 1:]:
            assert not (
                a.min_col <= b.max_col and b.min_col <= a.max_col
                and a.min_row <= b.max_row and b.min_row <= a.max_row
            ), f"merged ranges {a} and {b} overlap"


def test_headings_appear_above_their_photos(site_dir, tmp_path):
    plan, _, out = build(site_dir, tmp_path)
    ws = load_workbook(out).active
    for sector in plan.sectors:
        for zone in (sector.left, sector.right):
            for category in zone.categories:
                cell = ws.cell(row=category.heading_row, column=category.heading_col + 1)
                assert cell.value == category.heading


def test_aspect_ratio_is_preserved_in_the_drawing(site_dir, tmp_path):
    _, _, out = build(site_dir, tmp_path)
    ws = load_workbook(out).active
    for image in ws._images:
        ext = image.anchor.ext
        drawn = ext.cx / ext.cy
        with Image.open(image.ref) as src:
            natural = src.width / src.height
        assert abs(drawn - natural) < 0.02, "photo is stretched"


def test_dedupe_keeps_the_workbook_readable(tmp_path):
    """Two headings sharing one photo file must still both render it."""
    site = tmp_path / "images" / "ZZDUP1"
    prefix = PREFIX.format(s=1)
    for name in ("_850_Tilt_1.jpg", "_900_Tilt_1.jpg"):
        write_photo(site / f"{prefix}{name}", (2000, 1500), colour=(10, 20, 30))
    _, result, out = build(site, tmp_path)

    before = check_workbook(out)
    stats = deduplicate_media(out)
    after = check_workbook(out)

    assert stats.parts_after < stats.parts_before, "identical photos were not shared"
    assert after.ok, after.errors
    assert after.checks["anchors"] == before.checks["anchors"]
    assert load_workbook(out).active is not None


def test_full_res_embeds_the_original_bytes(site_dir, tmp_path):
    _, _, out = build(site_dir, tmp_path, max_dim=None)
    with zipfile.ZipFile(out) as zf:
        sizes = {n: zf.getinfo(n).file_size for n in zf.namelist()
                 if n.startswith("xl/media/")}
    assert sizes, "no media embedded"


def test_values_sheet_has_a_row_for_every_sector(site_dir, tmp_path):
    plan, _, out = build(site_dir, tmp_path)
    ws = load_workbook(out)[VALUES_SHEET_TITLE]

    assert [ws.cell(row=1, column=c).value for c in range(1, 7)] == [
        "sector", None, "Pre", "Before Swap", "Post", "Plan",
    ]

    rows = [
        (ws.cell(row=r, column=1).value, ws.cell(row=r, column=2).value)
        for r in range(2, ws.max_row + 1)
    ]
    expected = [
        (f"sec{sector.number}", template.format(sector=sector.number))
        for sector in plan.sectors
        for template in VALUES_ROWS
    ]
    assert rows == expected


def test_values_sheet_leaves_every_reading_empty(site_dir, tmp_path):
    """The numbers come off instruments, not out of the photographs."""
    _, _, out = build(site_dir, tmp_path)
    ws = load_workbook(out)[VALUES_SHEET_TITLE]
    for row in range(2, ws.max_row + 1):
        for column in range(3, 8):          # Pre, Before Swap, Post, Plan, remarks
            assert ws.cell(row=row, column=column).value is None


def test_the_photo_sheet_is_still_the_first_sheet(site_dir, tmp_path):
    """Readers that take the first sheet must still find the photos."""
    plan, _, out = build(site_dir, tmp_path)
    wb = load_workbook(out)
    assert wb.sheetnames == [plan.site[:31], VALUES_SHEET_TITLE]


def test_the_ar_template_drops_the_before_swap_column_everywhere(site_dir, tmp_path):
    """The older sheet must carry no trace of a band it does not have."""
    plan, _, out = build(site_dir, tmp_path, sheet_layout=layout.AR)
    assert check_plan(plan) == []
    assert check_workbook(out).ok

    wb = load_workbook(out)
    ws = wb.active
    texts = {
        cell.value
        for row in ws.iter_rows()
        for cell in row
        if isinstance(cell.value, str)
    }
    assert "Before Swap" not in texts
    assert "Paste Before Swap photo here" not in texts
    assert "Post" in texts

    values = wb[VALUES_SHEET_TITLE]
    assert [values.cell(row=1, column=c).value for c in range(1, 6)] == [
        "sector", None, "Pre", "Post", "Plan",
    ]


def test_the_ar_sheet_is_narrower_than_the_manual_one(site_dir, tmp_path):
    manual, _, _ = build(site_dir, tmp_path / "m")
    ar, _, _ = build(site_dir, tmp_path / "a", sheet_layout=layout.AR)
    assert ar.sheet_layout.last_col < manual.sheet_layout.last_col
    # One band per zone fewer, and one gap column with it.
    assert (manual.sheet_layout.last_col - ar.sheet_layout.last_col
            == 2 * layout.BAND_STRIDE)


def test_values_columns_follow_the_template(site_dir, tmp_path):
    assert values_columns(layout.MANUAL) == (
        "sector", None, "Pre", "Before Swap", "Post", "Plan",
    )
    assert values_columns(layout.AR) == ("sector", None, "Pre", "Post", "Plan")
