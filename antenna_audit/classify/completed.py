"""Learn from workbooks you have already filled in by hand.

A finished workbook is the best training data there is: every Post photo in it
sits under a heading a person chose, and the photo itself is stored inside the
file. So a completed sheet is a labelled dataset that needs no extra work from
anyone — point at the folder and the classifier learns what you actually did.

This matters more than the count suggests. The survey crew changed instruments
between rounds (a green Digi-Pas inclinometer in the Pre photos, a blue angle
gauge in the Post), so a model trained on Pre photos alone is looking for the
wrong object. Completed workbooks are the only large source of *Post*-round
examples, and they arrive already labelled.

Reading them back is not quite symmetrical with writing them. A photo pasted by
hand lands near its box but rarely exactly on it — measured across seven
workbooks, anchors sat on the band's first column most of the time but drifted
up to five columns either way. So a photo is assigned to the nearest column
band rather than an exact column, and anything too far from any band is ignored
as parked outside the layout.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from .. import layout

_XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS = {"xdr": _XDR, "r": _REL, "m": _MAIN}

# Where each column band starts, and which round of photography it holds.
BANDS = {
    layout.LEFT_PRE_COL0: "pre",
    layout.LEFT_BEFORE_COL0: "before",
    layout.LEFT_POST_COL0: "post",
    layout.RIGHT_PRE_COL0: "pre",
    layout.RIGHT_BEFORE_COL0: "before",
    layout.RIGHT_POST_COL0: "post",
}

# Workbooks built before the Before Swap band was added have only two bands per
# zone, and their columns mean something else entirely: column 9 was Post, not
# Before Swap, and column 25 was the right-hand Pre, not the right-hand Post.
# Reading one of those with the current map would mislabel every photo in it, so
# the old geometry is kept here verbatim rather than derived from ``layout``.
LEGACY_BANDS = {1: "pre", 9: "post", 17: "pre", 25: "post"}

# The header text that tells the two apart.  A sheet that names the middle band
# is a three-band sheet; anything else is read with the old map.
BEFORE_SWAP_HEADER = "Before Swap"

# How far a hand-pasted photo may sit from its band before we stop guessing.
MAX_COLUMN_DRIFT = 6


@dataclass(frozen=True)
class PlacedPhoto:
    """One photo a person put under one heading."""

    site: str
    sector: int
    row: str          # the sheet heading text, e.g. "Sec 1 Antenna M Tilt"
    kind: str         # "pre" or "post"
    digest: str
    suffix: str
    data: bytes

    @property
    def filename(self) -> str:
        return f"{self.site}_S{self.sector}_{self.digest[:12]}{self.suffix}"


def _column_index(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref).group(1)
    index = 0
    for char in letters:
        index = index * 26 + ord(char) - 64
    return index - 1


def _cell_text(cell, shared: list[str]) -> str | None:
    if cell.get("t") == "inlineStr":
        node = cell.find(f"{{{_MAIN}}}is/{{{_MAIN}}}t")
        return node.text if node is not None else None
    value = cell.find(f"{{{_MAIN}}}v")
    if value is None or value.text is None:
        return None
    if cell.get("t") == "s":
        index = int(value.text)
        return shared[index] if 0 <= index < len(shared) else None
    return value.text


def _read_sheet(
    archive: zipfile.ZipFile, sheet: str
) -> tuple[list[tuple[int, int, str]], dict[int, str]]:
    """Parse one sheet into its slot headings and the band map it was built to.

    Returns ``(headings, bands)`` where each heading is ``(row, column, text)``.
    """
    shared: list[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        shared = [
            "".join(t.text or "" for t in si.iter(f"{{{_MAIN}}}t"))
            for si in ET.fromstring(archive.read("xl/sharedStrings.xml"))
        ]
    found = []
    three_band = False
    root = ET.fromstring(archive.read(sheet))
    for row in root.iter(f"{{{_MAIN}}}row"):
        number = int(row.get("r"))
        for cell in row:
            text = _cell_text(cell, shared)
            if not text:
                continue
            if text.strip() == BEFORE_SWAP_HEADER:
                three_band = True
            elif text.startswith("Sec ") and not text.startswith("Sector"):
                found.append((number, _column_index(cell.get("r")), text.strip()))
    return found, BANDS if three_band else LEGACY_BANDS


def _nearest_band(
    column: int, bands: dict[int, str] | None = None
) -> tuple[int, str] | None:
    """Which band a hand-placed photo belongs to, or None if it is parked."""
    best, distance = None, MAX_COLUMN_DRIFT + 1
    for start, kind in (bands or BANDS).items():
        gap = abs(column - start)
        if gap < distance:
            best, distance = (start, kind), gap
    return best


def extract(workbook: Path) -> list[PlacedPhoto]:
    """Every photo in a completed workbook, with the heading it sits under."""
    site = workbook.name.split()[0]
    placed: list[PlacedPhoto] = []

    with zipfile.ZipFile(workbook) as archive:
        names = set(archive.namelist())
        sheets = sorted(
            n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)
        )
        if not sheets:
            return []
        headings, bands = _read_sheet(archive, sheets[0])

        for drawing in sorted(
            n for n in names if re.fullmatch(r"xl/drawings/drawing\d+\.xml", n)
        ):
            rels_name = posixpath.join(
                posixpath.dirname(drawing), "_rels",
                posixpath.basename(drawing) + ".rels",
            )
            if rels_name not in names:
                continue
            rels = {
                rel.get("Id"): rel.get("Target")
                for rel in ET.fromstring(archive.read(rels_name))
            }

            for anchor in ET.fromstring(archive.read(drawing)):
                blip = anchor.find(".//{*}blip")
                marker = anchor.find("xdr:from", _NS)
                if blip is None or marker is None:
                    continue
                target = rels.get(blip.get(f"{{{_REL}}}embed"))
                if not target:
                    continue
                part = (
                    target[1:] if target.startswith("/")
                    else posixpath.normpath(
                        posixpath.join(posixpath.dirname(drawing), target)
                    )
                )
                if part not in names:
                    continue

                column = int(marker.find("xdr:col", _NS).text)
                row_number = int(marker.find("xdr:row", _NS).text) + 1
                band = _nearest_band(column, bands)
                if band is None:
                    continue                      # parked outside the layout
                band_column, kind = band

                above = [
                    h for h in headings
                    if h[1] == band_column and h[0] < row_number
                ]
                if not above:
                    continue
                heading = max(above, key=lambda h: h[0])[2]
                sector_match = re.match(r"Sec (\d+)", heading)
                if not sector_match:
                    continue

                data = archive.read(part)
                placed.append(PlacedPhoto(
                    site=site, sector=int(sector_match.group(1)),
                    row=heading, kind=kind,
                    digest=hashlib.sha256(data).hexdigest(),
                    suffix=Path(part).suffix.lower() or ".jpg",
                    data=data,
                ))
    return placed


def extract_all(folder: Path) -> list[PlacedPhoto]:
    """Every placement across a folder of completed workbooks."""
    found: list[PlacedPhoto] = []
    for workbook in sorted(folder.glob("*.xlsx")):
        if workbook.name.startswith("~$"):
            continue                              # an Excel lock file
        found.extend(extract(workbook))
    return found
