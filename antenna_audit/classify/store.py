"""Remember what the user confirmed, and feed it back into training.

This is the part that makes the classifier work rather than merely exist.

Trained on the Pre photos alone, the model reaches 62% on Post photos and picks
the right mechanical-tilt shot in only 2 sectors out of 4 — because the survey
crew changed instruments between the two rounds. The Pre photos show a green
Digi-Pas inclinometer; the Post photos show a blue angle gauge. Any model that
learned "green device" on Pre is wrong on Post, and no amount of feature
engineering fixes a subject that simply isn't in the training data.

Confirmations do fix it. With a few already-confirmed sectors in the training
set, the same features reach 84% per-photo and pick the right photo for every
single-slot category. So every click in the review screen is stored here and
folded into the next fit, and accuracy climbs as the user works.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path

STORE_FILENAME = "confirmations.csv"
FIELDS = ["digest", "site", "sector", "filename", "category", "port"]


@dataclass(frozen=True)
class Confirmation:
    """One human decision about one photo."""

    digest: str          # content hash, so a re-uploaded copy is recognised
    site: str
    sector: str
    filename: str
    category: str
    port: str = ""       # set only for electrical-tilt photos

    def as_row(self) -> dict:
        return {
            "digest": self.digest, "site": self.site, "sector": self.sector,
            "filename": self.filename, "category": self.category, "port": self.port,
        }


def digest_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ConfirmationStore:
    """A CSV of confirmed labels, keyed by image content.

    Keyed by content hash rather than path so that re-uploading the same photo,
    or the same shot arriving under a different WhatsApp filename, still counts
    as already answered.
    """

    def __init__(self, directory: Path) -> None:
        self.path = directory / STORE_FILENAME
        self._by_digest: dict[str, Confirmation] = {}
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        with self.path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                if not row.get("digest"):
                    continue
                self._by_digest[row["digest"]] = Confirmation(
                    digest=row["digest"], site=row.get("site", ""),
                    sector=row.get("sector", ""), filename=row.get("filename", ""),
                    category=row.get("category", ""), port=row.get("port", ""),
                )

    def __len__(self) -> int:
        return len(self._by_digest)

    def get(self, path: Path) -> Confirmation | None:
        """The stored decision for this image, if it has been answered before."""
        return self._by_digest.get(digest_of(path))

    def add(self, path: Path, site: str, sector: str, category: str,
            port: str = "") -> Confirmation:
        record = Confirmation(
            digest=digest_of(path), site=site, sector=sector,
            filename=path.name, category=category, port=port,
        )
        self._by_digest[record.digest] = record
        return record

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for record in self._by_digest.values():
                writer.writerow(record.as_row())

    def training_rows(self, search_roots: list[Path]) -> list[tuple[Path, str, str]]:
        """Confirmed photos that can still be found on disk, for retraining.

        Returns (path, category, group) where group is the site, so grouped
        cross-validation keeps a site's photos together.
        """
        index: dict[str, Path] = {}
        for root in search_roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file() and not path.name.startswith("."):
                    try:
                        index.setdefault(digest_of(path), path)
                    except OSError:
                        continue
        rows = []
        for record in self._by_digest.values():
            path = index.get(record.digest)
            if path is not None and record.category:
                rows.append((path, record.category, record.site or "confirmed"))
        return rows
