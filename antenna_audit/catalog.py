"""Discover and classify field-survey photos on disk.

Filenames coming out of the survey tool look like::

    Sector_1_RF_Antenna_Photos_Sector_1_RF_Antenna_Photos_Ant_Sec_1__850_Tilt_1.jpg
    Sector_3_RF_Antenna_Photos_Sector_3_RF_Antenna_Photos_Ant_Sec_3_Antenna_M_Tilt_1.jpg
    Existing_Antenna_Location_over_Sec_2_Mobitel_Antenna_Location_1.png

The sector number and the category both live inside the name, and the trailing
``_<n>`` is a sequence number for repeat shots of the same subject.  Splitting
the category from that sequence number cannot be done by stripping the last
``_<digits>`` group, because several category names legitimately end in a digit
(``1800_Tilt_1``, ``RRU_2_Lable_Photo``).  We therefore match against a known
vocabulary, longest name first, and only treat what is left over as a sequence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}

# Categories placed on the left half of the sheet, in sheet order.
ELECTRICAL_TILT_CATEGORIES = [
    "850_Tilt",
    "900_Tilt",
    "1800_Tilt_1",
    "1800_Tilt_2",
    "2100_Tilt",
]

# Categories placed on the right half of the sheet, in sheet order.
MECHANICAL_AZIMUTH_CATEGORIES = [
    "Antenna_M_Tilt",
    "Antenna_Azimuth_Photo",
    "Antenna_Coverage_Photo",
]

# Everything else the survey produces.  Recognised so that the category/sequence
# split stays correct, but not placed on the sheet; reported as "unplaced".
OTHER_CATEGORIES = [
    "Antenna_Lable_Photo",
    "Antenna_ports_Photo",
    "Antenna_High_Photo",
    "Existing_TMA_Bracket_Loc",
    "TMA_Filter_Combiner_spit",
    "RRU_1_Lable_Photo",
    "RRU_2_Lable_Photo",
    "RRU_3_Lable_Photo",
    "Mobitel_Antenna_Location",
    "Additional_Photo",
]

KNOWN_CATEGORIES = (
    ELECTRICAL_TILT_CATEGORIES + MECHANICAL_AZIMUTH_CATEGORIES + OTHER_CATEGORIES
)

# Longest first so that "1800_Tilt_1" wins over a hypothetical "1800_Tilt".
_CATEGORY_BY_LENGTH = sorted(KNOWN_CATEGORIES, key=len, reverse=True)

# Human-facing heading text.  Mirrors the wording used in the reference
# workbook, with its inconsistent double spaces normalised away.
DISPLAY_NAMES = {
    "850_Tilt": "Sec {sector}_ 850 Tilt",
    "900_Tilt": "Sec {sector}_ 900 Tilt",
    "1800_Tilt_1": "Sec {sector}_ 1800 Tilt 1",
    "1800_Tilt_2": "Sec {sector}_ 1800 Tilt 2",
    "2100_Tilt": "Sec {sector}_ 2100 Tilt",
    "Antenna_M_Tilt": "Sec {sector} Antenna M Tilt",
    "Antenna_Azimuth_Photo": "Sec {sector} Antenna Azimuth Photo",
    "Antenna_Coverage_Photo": "Sec {sector} Antenna Coverage Photo",
}

_SECTOR_SCOPED = re.compile(r"Ant_Sec_(\d+)_+(?P<rest>.+)$")
_ANTENNA_LOCATION = re.compile(
    r"Existing_Antenna_Location_over_Sec_(\d+)_(?P<rest>.+)$"
)
# "Additional" shots belong to no sector and carry a group index before their
# sequence number: Existing_Antenna_Location_over_Additional_Photo_3_2 is the
# second shot of additional-photo group 3.  Matched explicitly so the group
# index is not mistaken for part of the category name.
_ADDITIONAL = re.compile(
    r"^Existing_Antenna_Location_over_Additional_Photo_(?P<group>\d+)"
    r"(?:_(?P<seq>\d+))?$"
)
_TRAILING_SEQ = re.compile(r"^(?P<cat>.+?)_(?P<seq>\d+)$")


def heading_for(category: str, sector: int) -> str:
    """Heading text shown above a category's photos."""
    template = DISPLAY_NAMES.get(category)
    if template is not None:
        return template.format(sector=sector)
    # Unknown category: fall back to a readable form of the raw token.
    return f"Sec {sector} {category.replace('_', ' ')}"


@dataclass(frozen=True)
class Photo:
    """One image file, resolved to the sector and category it documents."""

    path: Path
    sector: int
    category: str
    sequence: int

    @property
    def name(self) -> str:
        return self.path.name


def _split_category_and_sequence(rest: str) -> tuple[str, int]:
    """Split ``850_Tilt_1`` into ``("850_Tilt", 1)``.

    Known categories are matched as a prefix so that names ending in a digit are
    not mistaken for a sequence number.  Unknown categories fall back to
    stripping a trailing ``_<digits>``.
    """
    for category in _CATEGORY_BY_LENGTH:
        if rest == category:
            return category, 1
        if rest.startswith(category + "_"):
            tail = rest[len(category) + 1 :]
            if tail.isdigit():
                return category, int(tail)

    match = _TRAILING_SEQ.match(rest)
    if match:
        return match.group("cat"), int(match.group("seq"))
    return rest, 1


def classify(path: Path) -> Photo | None:
    """Resolve one file to a :class:`Photo`, or ``None`` if it is not a photo."""
    if path.name.startswith(".") or path.suffix.lower() not in IMAGE_SUFFIXES:
        return None

    stem = path.stem

    additional = _ADDITIONAL.match(stem)
    if additional:
        return Photo(
            path=path,
            sector=0,
            category="Additional_Photo",
            sequence=int(additional.group("seq") or 1),
        )

    for pattern in (_SECTOR_SCOPED, _ANTENNA_LOCATION):
        match = pattern.search(stem)
        if match:
            sector = int(match.group(1))
            category, sequence = _split_category_and_sequence(match.group("rest"))
            return Photo(path=path, sector=sector, category=category, sequence=sequence)

    # No sector in the name (e.g. "Existing_Antenna_Location_over_Additional_Photo_2_1").
    category, sequence = _split_category_and_sequence(stem)
    return Photo(path=path, sector=0, category=category, sequence=sequence)


@dataclass
class SiteInventory:
    """Everything found for one site, grouped by sector then category."""

    site: str
    root: Path
    photos: list[Photo] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)

    @property
    def sectors(self) -> list[int]:
        """Sector numbers that carry at least one placeable photo, ascending."""
        placeable = set(ELECTRICAL_TILT_CATEGORIES) | set(MECHANICAL_AZIMUTH_CATEGORIES)
        found = {p.sector for p in self.photos if p.sector and p.category in placeable}
        return sorted(found)

    def get(self, sector: int, category: str) -> list[Photo]:
        """All photos for one sector/category, ordered by sequence number."""
        matches = [
            p for p in self.photos if p.sector == sector and p.category == category
        ]
        return sorted(matches, key=lambda p: (p.sequence, p.name))

    def unplaced(self) -> dict[str, int]:
        """Counts per category for photos the sheet layout does not include."""
        placed = set(ELECTRICAL_TILT_CATEGORIES) | set(MECHANICAL_AZIMUTH_CATEGORIES)
        counts: dict[str, int] = {}
        for photo in self.photos:
            if photo.category not in placed:
                counts[photo.category] = counts.get(photo.category, 0) + 1
        return dict(sorted(counts.items()))

    def unknown_categories(self) -> set[str]:
        """Categories that are not in the known vocabulary at all."""
        return {p.category for p in self.photos if p.category not in KNOWN_CATEGORIES}


def scan_site(site_dir: Path) -> SiteInventory:
    """Build an inventory for a single site directory."""
    inventory = SiteInventory(site=site_dir.name, root=site_dir)
    for path in sorted(site_dir.iterdir()):
        if not path.is_file():
            continue
        photo = classify(path)
        if photo is None:
            if not path.name.startswith("."):
                inventory.skipped.append(path)
        else:
            inventory.photos.append(photo)
    return inventory


def scan_root(images_root: Path) -> list[SiteInventory]:
    """Build inventories for every site directory under ``images_root``."""
    sites = []
    for child in sorted(images_root.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            sites.append(scan_site(child))
    return sites
