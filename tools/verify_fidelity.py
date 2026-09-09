"""Prove the right photo sits under the right heading, in every workbook.

This deliberately shares no code with the builder.  It re-parses the saved
.xlsx from scratch, matches each embedded picture back to a source photo by
comparing PIXELS (so downscaling and re-encoding cannot fool it), and then
checks that the matched source file's NAME agrees with the heading the picture
was placed under.

    python tools/verify_fidelity.py <images-root> <output-dir>

Needs numpy in addition to the app's own dependencies.
"""

from __future__ import annotations

import io
import posixpath
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image, ImageOps

NS = {
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}
HEADING_TO_TOKEN = {
    "850 Tilt": "850_Tilt", "900 Tilt": "900_Tilt",
    "1800 Tilt 1": "1800_Tilt_1", "1800 Tilt 2": "1800_Tilt_2",
    "2100 Tilt": "2100_Tilt", "Antenna M Tilt": "Antenna_M_Tilt",
    "Antenna Azimuth Photo": "Antenna_Azimuth_Photo",
    "Antenna Coverage Photo": "Antenna_Coverage_Photo",
}
LEFT_TOKENS = {"850_Tilt", "900_Tilt", "1800_Tilt_1", "1800_Tilt_2", "2100_Tilt"}
# Column index -> which half of the sheet the Pre band belongs to.
PRE_BANDS = {1: "left", 17: "right"}
POST_BANDS = {9, 25}
# Mean squared difference below which two 32x32 normalised thumbnails are the
# same photograph.  Comfortably above JPEG re-encoding noise, far below the
# distance between two different photographs.
SAME_PHOTO = 0.01


def signature(image: Image.Image) -> np.ndarray:
    small = ImageOps.exif_transpose(image).convert("L").resize((32, 32), Image.LANCZOS)
    array = np.asarray(small, dtype=np.float32)
    return (array - array.mean()) / (array.std() + 1e-6)


def column_index(ref: str) -> int:
    index = 0
    for char in re.match(r"([A-Z]+)", ref).group(1):
        index = index * 26 + ord(char) - 64
    return index - 1


def verify(images_root: Path, output_dir: Path) -> int:
    problems: list[str] = []
    checked = 0

    for workbook in sorted(output_dir.glob("*.xlsx")):
        site = workbook.name.split()[0]
        source_dir = images_root / site
        if not source_dir.is_dir():
            problems.append(f"{site}: no source folder at {source_dir}")
            continue

        sources = {}
        for photo in sorted(source_dir.iterdir()):
            if photo.is_file() and not photo.name.startswith("."):
                try:
                    with Image.open(photo) as img:
                        sources[photo.name] = signature(img)
                except Exception:
                    pass

        zf = zipfile.ZipFile(workbook)
        names = set(zf.namelist())
        embedded = {}
        for name in names:
            if name.startswith("xl/media/"):
                with Image.open(io.BytesIO(zf.read(name))) as img:
                    embedded[name] = signature(img)

        rels = {
            rel.get("Id"): rel.get("Target")
            for rel in ET.fromstring(zf.read("xl/drawings/_rels/drawing1.xml.rels"))
        }

        headings = []
        sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
        for row in sheet.findall(f".//{{{NS['m']}}}row"):
            row_number = int(row.get("r"))
            for cell in row:
                if cell.get("t") != "inlineStr":
                    continue
                text = cell.find(f"{{{NS['m']}}}is/{{{NS['m']}}}t")
                value = text.text if text is not None else None
                if value and value.startswith("Sec ") and not value.startswith("Sector"):
                    headings.append((row_number, column_index(cell.get("r")), value))

        for anchor in ET.fromstring(zf.read("xl/drawings/drawing1.xml")):
            blip = anchor.find(".//{*}blip")
            if blip is None:
                continue
            target = rels[blip.get(f"{{{NS['r']}}}embed")]
            part = (
                target[1:] if target.startswith("/")
                else posixpath.normpath(posixpath.join("xl/drawings", target))
            )
            marker = anchor.find("xdr:from", NS)
            col = int(marker.find("xdr:col", NS).text)
            row_number = int(marker.find("xdr:row", NS).text) + 1

            if col in POST_BANDS:
                problems.append(f"{site}: a picture sits in a Post band at row {row_number}")
                continue

            above = [h for h in headings if h[1] == col and h[0] < row_number]
            if not above:
                problems.append(
                    f"{site}: picture at row {row_number} column {col} has no heading"
                )
                continue
            _, heading_col, heading = max(above, key=lambda h: h[0])
            checked += 1

            match = re.match(r"Sec (\d+)[_ ]+(.+)", heading)
            sector, label = int(match.group(1)), match.group(2).strip()
            token = HEADING_TO_TOKEN.get(label)
            if token is None:
                problems.append(f"{site}: unrecognised heading {heading!r}")
                continue

            expected_half = "left" if token in LEFT_TOKENS else "right"
            if PRE_BANDS.get(col) != expected_half:
                problems.append(f"{site}: {heading!r} is not in the {expected_half} half")

            # Every source photo whose pixels match; duplicates under different
            # names are legitimate, so any one of them agreeing is enough.
            candidates = [
                name for name, sig in sources.items()
                if float(np.mean((sig - embedded[part]) ** 2)) < SAME_PHOTO
            ]
            if not candidates:
                problems.append(f"{site}: {heading!r} shows a photo not in the survey")
                continue
            wanted = re.compile(rf"Ant_Sec_{sector}_+{re.escape(token)}(_\d+)?\.")
            if not any(wanted.search(name) for name in candidates):
                problems.append(f"{site}: {heading!r} shows {candidates[0]}")

    print(f"pictures verified against their heading: {checked}")
    print(f"problems: {len(problems)}")
    for problem in problems:
        print(f"  - {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    raise SystemExit(verify(Path(sys.argv[1]), Path(sys.argv[2])))
