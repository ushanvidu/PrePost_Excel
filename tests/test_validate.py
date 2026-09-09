"""The validators must actually fire. A check that never triggers proves nothing."""

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

from antenna_audit import layout
from antenna_audit.dedupe import deduplicate_media
from antenna_audit.imaging import ImagePreparer
from antenna_audit.plan import build_plan
from antenna_audit.validate import check_plan, check_workbook
from antenna_audit.workbook import write_workbook
from tests.test_plan import ELECTRICAL_TILT_CATEGORIES, make_inventory


def test_overlapping_boxes_are_detected(monkeypatch):
    """Shrinking the gap until a box eats the next heading must be caught."""
    plan = build_plan(make_inventory({1: {c: 1 for c in ELECTRICAL_TILT_CATEGORIES}}))
    assert check_plan(plan) == []

    # Drag one category's boxes down over the heading that follows them.
    victim = plan.sectors[0].left.categories[1]
    for slot in plan.sectors[0].left.categories[0].slots:
        object.__setattr__(slot.box, "rows", slot.box.rows + 60)

    errors = check_plan(plan)
    assert errors, "an overlap this large must not pass"
    assert any("overlap" in e for e in errors)
    assert any(victim.heading in e for e in errors)


def test_box_above_its_heading_is_detected():
    plan = build_plan(make_inventory({1: {"850_Tilt": 1}}))
    category = plan.sectors[0].left.categories[0]
    object.__setattr__(category.slots[0].box, "row0", category.heading_row - 1)
    assert any("heading" in e for e in check_plan(plan))


def test_box_in_the_wrong_column_is_detected():
    plan = build_plan(make_inventory({1: {"850_Tilt": 1}}))
    slot = plan.sectors[0].left.categories[0].slots[0]
    object.__setattr__(slot.box, "col0", layout.RIGHT_POST_COL0)
    assert any("expected" in e for e in check_plan(plan))


def _tiny_site(tmp_path: Path) -> Path:
    site = tmp_path / "images" / "ZZV1"
    site.mkdir(parents=True)
    prefix = "Sector_1_RF_Antenna_Photos_Sector_1_RF_Antenna_Photos_Ant_Sec_1_"
    Image.new("RGB", (800, 600), (30, 90, 140)).save(site / f"{prefix}_850_Tilt_1.jpg")
    Image.new("RGB", (600, 800), (140, 90, 30)).save(
        site / f"{prefix}Antenna_M_Tilt_1.jpg"
    )
    return site


def _build(tmp_path: Path):
    from antenna_audit import catalog

    site = _tiny_site(tmp_path)
    preparer = ImagePreparer(tmp_path / "work")
    plan = build_plan(catalog.scan_site(site), preparer)
    out = tmp_path / "v.xlsx"
    write_workbook(plan, out, preparer)
    return out


def test_a_dangling_relationship_is_detected(tmp_path):
    """Deleting a media part must make check_workbook fail."""
    out = _build(tmp_path)
    assert check_workbook(out).ok

    with zipfile.ZipFile(out) as zf:
        entries = [(i.filename, zf.read(i.filename)) for i in zf.infolist()]
    broken = tmp_path / "broken.xlsx"
    with zipfile.ZipFile(broken, "w", zipfile.ZIP_DEFLATED) as zw:
        dropped = False
        for name, data in entries:
            if name.startswith("xl/media/") and not dropped:
                dropped = True
                continue
            zw.writestr(name, data)

    report = check_workbook(broken)
    assert not report.ok
    assert any("missing part" in e for e in report.errors)


def test_overlapping_merges_are_detected(tmp_path):
    """Excel refuses such a file, so the validator must refuse it first."""
    out = _build(tmp_path)
    assert check_workbook(out).ok

    with zipfile.ZipFile(out) as zf:
        entries = [(i.filename, zf.read(i.filename)) for i in zf.infolist()]
    broken = tmp_path / "merged.xlsx"
    with zipfile.ZipFile(broken, "w", zipfile.ZIP_DEFLATED) as zw:
        for name, data in entries:
            if name == "xl/worksheets/sheet1.xml":
                text = data.decode()
                text = text.replace(
                    "<mergeCells ", '<mergeCells zz="1" ', 1
                ) if "<mergeCells " in text else text
                # Inject a merge that straddles two existing ones.
                text = text.replace(
                    "</mergeCells>", '<mergeCell ref="A1:AG40"/></mergeCells>'
                )
                data = text.encode()
            zw.writestr(name, data)

    report = check_workbook(broken)
    assert not report.ok
    assert any("overlap" in e for e in report.errors)


def test_dedupe_leaves_external_links_alone(tmp_path):
    """A hyperlink whose URL ends in a deduplicated filename must not be rewritten."""
    out = _build(tmp_path)
    url = "https://example.com/report/image1.jpeg"

    with zipfile.ZipFile(out) as zf:
        entries = [(i.filename, zf.read(i.filename)) for i in zf.infolist()]
    seeded = tmp_path / "seeded.xlsx"
    with zipfile.ZipFile(seeded, "w", zipfile.ZIP_DEFLATED) as zw:
        for name, data in entries:
            if name == "xl/drawings/_rels/drawing1.xml.rels":
                text = data.decode().replace(
                    "</Relationships>",
                    '<Relationship Id="rIdLink" Target="%s" TargetMode="External" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                    'relationships/hyperlink"/></Relationships>' % url,
                )
                data = text.encode()
            # Duplicate a media part so dedupe has work to do.
            zw.writestr(name, data)
        media = [(n, d) for n, d in entries if n.startswith("xl/media/")]
        if media:
            zw.writestr("xl/media/copy_of_image.jpeg", media[0][1])

    deduplicate_media(seeded)

    with zipfile.ZipFile(seeded) as zf:
        rels = ET.fromstring(zf.read("xl/drawings/_rels/drawing1.xml.rels"))
    link = [r for r in rels if r.get("Id") == "rIdLink"]
    assert link and link[0].get("Target") == url, "an external URL was rewritten"


def test_exotic_colour_modes_are_re_encoded(tmp_path):
    """A CMYK JPEG must not reach Excel untouched."""
    source = tmp_path / "cmyk.jpg"
    Image.new("CMYK", (800, 600), (10, 20, 30, 40)).save(source)

    prepared = ImagePreparer(tmp_path / "work").prepare(source)
    assert prepared is not None
    assert not prepared.reused_original, "CMYK original was passed straight through"
    with Image.open(prepared.path) as out:
        assert out.mode in ("RGB", "L")
