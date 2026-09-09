"""Structural checks on a generated workbook.

These verify the things a reader would notice immediately and that unit tests on
the plan alone cannot prove: that nothing on the sheet overlaps anything else,
that every drawing points at an image part that is actually present, and that no
two merged cell ranges intersect — Excel refuses to open a workbook with
overlapping merges, so that one check is the difference between a file that
opens and a file that does not.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from . import layout
from .plan import SheetPlan

_NS = {
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


@dataclass
class ValidationReport:
    path: Path
    errors: list[str] = field(default_factory=list)
    checks: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def _overlaps(a, b) -> bool:
    """True when two (col0, row0, cols, rows) rectangles intersect."""
    ac0, ar0, ac, ar = a
    bc0, br0, bc, br = b
    return (
        ac0 < bc0 + bc and bc0 < ac0 + ac and ar0 < br0 + br and br0 < ar0 + ar
    )


def check_plan(plan: SheetPlan) -> list[str]:
    """Find boxes that collide, or that stray outside their column band."""
    errors: list[str] = []
    boxes: list[tuple[tuple[int, int, int, int], str]] = []

    for sector in plan.sectors:
        # Banner and header rows span the sheet, so a box that runs into one is
        # just as broken as two boxes that collide.  They must be in the set.
        full_width = layout.LAST_COL + 1
        for row, what in (
            (sector.banner_row, f"S{sector.number} banner"),
            (sector.zone_header_row, f"S{sector.number} zone header"),
            (sector.prepost_header_row, f"S{sector.number} Pre/Post header"),
        ):
            boxes.append(((0, row, full_width, 1), what))

        for zone in (sector.left, sector.right):
            for category in zone.categories:
                # The heading itself occupies a row in both the Pre and Post
                # bands; a box must never be drawn over it.
                for col0 in (category.heading_col, category.heading_post_col):
                    boxes.append((
                        (col0, category.heading_row, layout.BOX_COLS, 1),
                        f"S{sector.number} heading '{category.heading}'",
                    ))
                for slot in category.slots:
                    box = slot.box
                    label = (
                        f"S{sector.number} {category.heading} [{slot.kind}]"
                        f" @r{box.row0}"
                    )
                    boxes.append(((box.col0, box.row0, box.cols, box.rows), label))

                    expected = (
                        zone.pre_col0 if slot.kind == "pre" else zone.post_col0
                    )
                    if box.col0 != expected:
                        errors.append(
                            f"{label}: column {box.col0}, expected {expected}"
                        )
                    if box.row0 <= category.heading_row:
                        errors.append(f"{label}: box starts on or above its heading")

    boxes.sort(key=lambda item: (item[0][1], item[0][0]))
    for i, (rect, label) in enumerate(boxes):
        for other_rect, other_label in boxes[i + 1 :]:
            if other_rect[1] >= rect[1] + rect[3]:
                break  # sorted by row: nothing further down can overlap
            if _overlaps(rect, other_rect):
                errors.append(f"overlap: {label} collides with {other_label}")

    # Every sector's content must end before the next sector's banner.
    for earlier, later in zip(plan.sectors, plan.sectors[1:]):
        if earlier.end_row >= later.banner_row:
            errors.append(
                f"sector {earlier.number} ends at row {earlier.end_row}, "
                f"but sector {later.number} starts at {later.banner_row}"
            )
    return errors


class _SheetGeometry:
    """Real column x-offsets and row y-offsets, read from the saved sheet.

    The overlap test below has to compare pictures in the coordinate space the
    reader will actually use.  Assuming uniform cells would place a picture in a
    space that does not exist — narrow gap columns and taller banner rows would
    both be ignored — so the widths and heights are taken from the file itself.
    """

    def __init__(self, sheet_xml: bytes) -> None:
        root = ET.fromstring(sheet_xml)
        fmt = root.find("{*}sheetFormatPr")
        default_w = layout.BOX_COL_PX
        default_h = layout.ROW_PX
        if fmt is not None:
            if fmt.get("defaultColWidth"):
                default_w = float(fmt.get("defaultColWidth")) * layout.MAX_DIGIT_WIDTH
            if fmt.get("defaultRowHeight"):
                default_h = float(fmt.get("defaultRowHeight")) * 96 / 72
        self._default_w = default_w
        self._default_h = default_h

        self._widths: dict[int, float] = {}
        for col in root.findall(".//{*}col"):
            width = float(col.get("width", 0)) * layout.MAX_DIGIT_WIDTH
            for index in range(int(col.get("min")) - 1, int(col.get("max"))):
                self._widths[index] = width

        self._heights: dict[int, float] = {}
        for row in root.findall(".//{*}row"):
            if row.get("ht"):
                self._heights[int(row.get("r")) - 1] = float(row.get("ht")) * 96 / 72

    def col_x(self, col: int) -> float:
        return sum(self._widths.get(i, self._default_w) for i in range(col))

    def row_y(self, row: int) -> float:
        return sum(self._heights.get(i, self._default_h) for i in range(row))


def _sheet_for_drawing(zf: zipfile.ZipFile, names: set[str], drawing: str) -> str | None:
    """Find the worksheet part whose relationships point at ``drawing``."""
    for name in sorted(names):
        if not re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name):
            continue
        rels_name = f"xl/worksheets/_rels/{posixpath.basename(name)}.rels"
        if rels_name not in names:
            continue
        for rel in ET.fromstring(zf.read(rels_name)):
            target = rel.get("Target") or ""
            resolved = (
                target[1:]
                if target.startswith("/")
                else posixpath.normpath(posixpath.join("xl/worksheets", target))
            )
            if resolved == drawing:
                return name
    return None


def check_workbook(path: Path) -> ValidationReport:
    """Open a saved .xlsx and verify its drawing layer is internally consistent."""
    report = ValidationReport(path=path)
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        drawings = [n for n in names if re.fullmatch(r"xl/drawings/drawing\d+\.xml", n)]
        if not drawings:
            report.errors.append("no drawing part: the workbook holds no images")
            return report

        total_anchors = 0
        for drawing in drawings:
            rels_name = posixpath.join(
                posixpath.dirname(drawing), "_rels", posixpath.basename(drawing) + ".rels"
            )
            if rels_name not in names:
                report.errors.append(f"{drawing}: relationship part missing")
                continue
            rels = {
                rel.get("Id"): rel.get("Target")
                for rel in ET.fromstring(zf.read(rels_name))
            }

            sheet_name = _sheet_for_drawing(zf, names, drawing)
            if sheet_name is None:
                report.errors.append(f"{drawing}: no worksheet references this drawing")
                continue
            geometry = _SheetGeometry(zf.read(sheet_name))

            root = ET.fromstring(zf.read(drawing))
            rects: list[tuple[tuple[float, float, float, float], str]] = []
            for anchor in root:
                blip = anchor.find(".//{*}blip")
                if blip is None:
                    continue
                total_anchors += 1
                rid = blip.get(f"{{{_NS['r']}}}embed")
                target = rels.get(rid)
                if target is None:
                    report.errors.append(f"{drawing}: anchor references unknown {rid}")
                    continue
                resolved = (
                    target[1:]
                    if target.startswith("/")
                    else posixpath.normpath(
                        posixpath.join(posixpath.dirname(drawing), target)
                    )
                )
                if resolved not in names:
                    report.errors.append(f"{drawing}: {rid} -> missing part {resolved}")

                marker = anchor.find("xdr:from", _NS)
                ext = anchor.find("xdr:ext", _NS)
                if marker is None or ext is None:
                    continue
                col = int(marker.find("xdr:col", _NS).text)
                row = int(marker.find("xdr:row", _NS).text)
                col_off = int(marker.find("xdr:colOff", _NS).text)
                row_off = int(marker.find("xdr:rowOff", _NS).text)
                x0 = geometry.col_x(col) + col_off / layout.EMU_PER_PX
                y0 = geometry.row_y(row) + row_off / layout.EMU_PER_PX
                w = int(ext.get("cx")) / layout.EMU_PER_PX
                h = int(ext.get("cy")) / layout.EMU_PER_PX
                if w <= 0 or h <= 0:
                    report.errors.append(f"{drawing}: {rid} has a zero-sized extent")
                rects.append(((x0, y0, w, h), rid))

            rects.sort(key=lambda item: (item[0][1], item[0][0]))
            for i, (rect, rid) in enumerate(rects):
                for other, other_rid in rects[i + 1 :]:
                    if other[1] >= rect[1] + rect[3]:
                        break
                    if (
                        rect[0] < other[0] + other[2]
                        and other[0] < rect[0] + rect[2]
                        and rect[1] < other[1] + other[3]
                        and other[1] < rect[1] + rect[3]
                    ):
                        report.errors.append(
                            f"{drawing}: pictures {rid} and {other_rid} overlap"
                        )

        merges = _merged_ranges(zf, names)
        for a, b in _intersecting(merges):
            report.errors.append(
                f"merged ranges {_a1(a)} and {_a1(b)} overlap — "
                "Excel will refuse to open this file"
            )

        media = {n for n in names if n.startswith("xl/media/")}
        report.checks = {
            "anchors": total_anchors,
            "media_parts": len(media),
            "drawings": len(drawings),
            "merged_ranges": len(merges),
        }
    return report


_CELL = re.compile(r"([A-Z]+)(\d+)")


def _col_index(letters: str) -> int:
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return index


def _merged_ranges(zf: zipfile.ZipFile, names: set[str]) -> list[tuple[int, int, int, int]]:
    """Every merged range in every worksheet, as (col0, row0, col1, row1)."""
    ranges: list[tuple[int, int, int, int]] = []
    for name in sorted(names):
        if not re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name):
            continue
        root = ET.fromstring(zf.read(name))
        for merge in root.findall(".//{*}mergeCell"):
            ref = merge.get("ref") or ""
            cells = _CELL.findall(ref)
            if len(cells) != 2:
                continue
            (c0, r0), (c1, r1) = cells
            ranges.append((_col_index(c0), int(r0), _col_index(c1), int(r1)))
    return ranges


def _intersecting(ranges):
    """Yield every pair of merged ranges that overlap."""
    ordered = sorted(ranges, key=lambda r: (r[1], r[0]))
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if b[1] > a[3]:
                break  # sorted by first row: nothing later can reach back
            if a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]:
                yield a, b


def _a1(rect) -> str:
    def letters(index: int) -> str:
        out = ""
        while index:
            index, rem = divmod(index - 1, 26)
            out = chr(65 + rem) + out
        return out
    c0, r0, c1, r1 = rect
    return f"{letters(c0)}{r0}:{letters(c1)}{r1}"
