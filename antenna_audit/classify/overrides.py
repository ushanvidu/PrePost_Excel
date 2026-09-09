"""Photos you supplied yourself for a slot, replacing whatever was proposed.

Separate from the confirmation store, which records "this photo already in the
sector belongs in this slot". This records "none of the photos here are right —
use this one instead", where the file comes from anywhere on your machine.

An override is keyed by site, sector and sheet row rather than by image content,
because the point is to name a slot and say what belongs in it. Replacing a slot
therefore replaces cleanly: the previous upload for that slot is removed with it.

**Only files this app copied into its own uploads folder are ever deleted.** The
photo you pick is copied in, never moved, so your originals are untouched.
"""

from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass
from pathlib import Path

STORE_FILENAME = "manual_overrides.csv"
UPLOAD_DIRNAME = "uploads"
FIELDS = ["site", "sector", "row", "path", "original_name"]


@dataclass(frozen=True)
class Override:
    site: str
    sector: str
    row: str
    path: Path
    original_name: str = ""


class OverrideStore:
    """Slot-keyed manual replacements, persisted next to the model."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / STORE_FILENAME
        self.uploads = directory / UPLOAD_DIRNAME
        self._by_slot: dict[tuple[str, str, str], Override] = {}
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        with self.path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                if not row.get("row"):
                    continue
                key = (row.get("site", ""), row.get("sector", ""), row["row"])
                self._by_slot[key] = Override(
                    site=key[0], sector=key[1], row=key[2],
                    path=Path(row.get("path", "")),
                    original_name=row.get("original_name", ""),
                )

    def __len__(self) -> int:
        return len(self._by_slot)

    def get(self, site: str, sector: str, row: str) -> Override | None:
        """The override for one slot, if its file is still on disk."""
        record = self._by_slot.get((site, sector, row))
        if record is None:
            return None
        if not record.path.is_file():
            return None            # cleaned up behind our back; fall back to the model
        return record

    def for_sector(self, site: str, sector: str) -> dict[str, Override]:
        return {
            key[2]: value for key, value in self._by_slot.items()
            if key[0] == site and key[1] == sector and value.path.is_file()
        }

    def save_upload(
        self, site: str, sector: str, row: str,
        source: Path, original_name: str,
    ) -> Override:
        """Copy a chosen file in as this slot's photo, replacing any previous one.

        The previous upload *for this slot* is deleted, because it is a copy this
        app made and nothing else refers to it. Files outside the uploads folder
        are never touched.
        """
        self.remove(site, sector, row)

        target_dir = self.uploads / _safe(site) / _safe(sector)
        target_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(original_name).suffix.lower() or source.suffix.lower() or ".jpg"
        target = target_dir / f"{_safe(row)}{suffix}"
        shutil.copyfile(source, target)

        record = Override(site=site, sector=sector, row=row, path=target,
                          original_name=original_name)
        self._by_slot[(site, sector, row)] = record
        return record

    def remove(self, site: str, sector: str, row: str) -> bool:
        """Drop a slot's override and delete the copy this app made."""
        record = self._by_slot.pop((site, sector, row), None)
        if record is None:
            return False
        try:
            # Guard: only ever unlink inside our own uploads folder.
            if record.path.is_file() and self.uploads in record.path.resolve().parents:
                record.path.unlink()
        except OSError:
            pass
        return True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for record in self._by_slot.values():
                writer.writerow({
                    "site": record.site, "sector": record.sector, "row": record.row,
                    "path": str(record.path), "original_name": record.original_name,
                })


def _safe(text: str) -> str:
    """A filesystem-safe fragment; slot and sector names are ours, not user text."""
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in text)
    return cleaned[:60] or "x"
