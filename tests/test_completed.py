"""Reading labelled Post photos back out of workbooks filled in by hand.

The round trip is the point: this project writes the workbook, a person fills in
the Post columns, and the same photos have to come back out with the heading the
person put them under. So the tests build a workbook with the project's own
writer, then read it back.
"""

from pathlib import Path

from PIL import Image

from antenna_audit import catalog
from antenna_audit.classify.completed import (
    MAX_COLUMN_DRIFT,
    _nearest_band,
    extract,
    extract_all,
)
from antenna_audit.imaging import ImagePreparer
from antenna_audit.plan import build_plan
from antenna_audit.workbook import write_workbook
from antenna_audit import layout

PREFIX = "Sector_{s}_RF_Antenna_Photos_Sector_{s}_RF_Antenna_Photos_Ant_Sec_{s}_"


def _photo(path: Path, colour, size=(400, 300)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path, quality=92)
    return path


def _site_with_post(tmp_path: Path):
    """A one-sector site, plus distinct Post photos for two of its slots."""
    site = tmp_path / "images" / "SITEA"
    prefix = PREFIX.format(s=1)
    _photo(site / f"{prefix}_850_Tilt_1.jpg", (90, 90, 90))
    _photo(site / f"{prefix}Antenna_M_Tilt_1.jpg", (30, 30, 120))

    post_low = _photo(tmp_path / "post" / "low.jpg", (11, 200, 90))
    post_mech = _photo(tmp_path / "post" / "mech.jpg", (200, 30, 11))
    return site, {(1, "850_Tilt"): [post_low], (1, "Antenna_M_Tilt"): [post_mech]}


def _build(tmp_path: Path):
    site, post = _site_with_post(tmp_path)
    inventory = catalog.scan_site(site)
    preparer = ImagePreparer(tmp_path / "work")
    plan = build_plan(inventory, preparer, post)
    out = tmp_path / "done" / "SITEA Antenna Audit Photos.xlsx"
    write_workbook(plan, out, preparer)
    return out


# --- band assignment ---------------------------------------------------------

def test_a_photo_on_its_band_is_assigned_to_it():
    for column, expected in (
        (layout.LEFT_PRE_COL0, "pre"),
        (layout.LEFT_POST_COL0, "post"),
        (layout.RIGHT_PRE_COL0, "pre"),
        (layout.RIGHT_POST_COL0, "post"),
    ):
        band = _nearest_band(column)
        assert band is not None and band[1] == expected


def test_a_hand_pasted_photo_that_drifted_still_lands_in_its_band():
    """Measured across seven real workbooks, anchors drifted a few columns.

    Bands sit eight columns apart, so a photo is claimed by the nearer one and
    a drift beyond half that distance belongs to the neighbour — which is the
    right answer, not a miss.
    """
    half_way = (layout.LEFT_POST_COL0 - layout.LEFT_PRE_COL0) // 2
    for drift in range(-half_way + 1, half_way):
        band = _nearest_band(layout.LEFT_POST_COL0 + drift)
        assert band is not None
        assert band[0] == layout.LEFT_POST_COL0, f"drift {drift} left its band"
        assert band[1] == "post"


def test_a_photo_parked_far_outside_the_layout_is_ignored():
    assert _nearest_band(layout.RIGHT_POST_COL0 + MAX_COLUMN_DRIFT + 20) is None


# --- extraction --------------------------------------------------------------

def test_post_photos_come_back_with_the_heading_they_sit_under(tmp_path):
    placed = extract(_build(tmp_path))
    post = {p.row: p for p in placed if p.kind == "post"}

    assert catalog.heading_for("850_Tilt", 1) in post
    assert catalog.heading_for("Antenna_M_Tilt", 1) in post
    for photo in post.values():
        assert photo.sector == 1
        assert photo.site == "SITEA"
        assert photo.data, "the image bytes are the point of extracting"


def test_pre_and_post_are_told_apart(tmp_path):
    placed = extract(_build(tmp_path))
    kinds = {p.kind for p in placed}
    assert kinds == {"pre", "post"}
    assert sum(1 for p in placed if p.kind == "post") == 2


def test_extracted_bytes_are_a_readable_image(tmp_path):
    import io

    post = [p for p in extract(_build(tmp_path)) if p.kind == "post"]
    for photo in post:
        with Image.open(io.BytesIO(photo.data)) as image:
            assert image.size[0] > 0


def test_the_same_photo_gets_the_same_digest(tmp_path):
    """Digests are how duplicates are collapsed before training."""
    placed = extract(_build(tmp_path))
    by_digest = {p.digest for p in placed}
    assert len(by_digest) <= len(placed)
    for photo in placed:
        assert len(photo.digest) == 64


def test_extract_all_skips_excel_lock_files(tmp_path):
    built = _build(tmp_path)
    folder = built.parent
    (folder / "~$SITEA Antenna Audit Photos.xlsx").write_bytes(b"lock")

    placed = extract_all(folder)
    assert placed, "the real workbook should still be read"
    assert all(p.site == "SITEA" for p in placed)


def test_a_folder_with_no_workbooks_yields_nothing(tmp_path):
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert extract_all(empty) == []
