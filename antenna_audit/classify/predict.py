"""Turn a folder of unlabelled Post photos into ranked slot candidates.

The output is deliberately a *ranking*, not a verdict. Three of the four slots
take exactly one photo per sector, so what matters is whether the right photo
comes top of its column — a much easier bar than classifying every photo
correctly, and the one the review screen is built around.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import labels
from .features import describe
from .store import ConfirmationStore

# Categories that take exactly one photo per sector.
SINGLE_SLOT = [labels.MECHANICAL_TILT, labels.AZIMUTH, labels.COVERAGE]

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


@dataclass
class Candidate:
    """One photo's score for one slot."""

    path: Path
    score: float
    confirmed: bool = False

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class SectorPrediction:
    """Ranked candidates for every slot in one sector."""

    sector: str
    photos: list[Path] = field(default_factory=list)
    ranked: dict[str, list[Candidate]] = field(default_factory=dict)
    best_guess: dict[Path, str] = field(default_factory=dict)

    def top(self, category: str, n: int = 3) -> list[Candidate]:
        return self.ranked.get(category, [])[:n]

    def electrical_tilt_photos(self) -> list[Candidate]:
        """Photos whose most likely class is electrical tilt, best first."""
        chosen = [
            c for c in self.ranked.get(labels.ELECTRICAL_TILT, [])
            if self.best_guess.get(c.path) == labels.ELECTRICAL_TILT or c.confirmed
        ]
        return chosen


def load_model(model_dir: Path):
    import joblib

    from .train import MODEL_FILENAME

    payload = joblib.load(model_dir / MODEL_FILENAME)
    return payload["model"]


def sector_photos(sector_dir: Path) -> list[Path]:
    return sorted(
        p for p in sector_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
        and not p.name.startswith(".")
    )


def predict_sector(
    sector_dir: Path, model, store: ConfirmationStore | None = None,
) -> SectorPrediction:
    """Score every photo in a sector folder against every slot."""
    prediction = SectorPrediction(sector=sector_dir.name)
    photos = sector_photos(sector_dir)
    prediction.photos = photos
    if not photos:
        return prediction

    features = np.vstack([describe(p) for p in photos])
    probabilities = model.predict_proba(features)
    classes = list(model.classes_)

    for category in labels.CATEGORIES:
        if category not in classes:
            continue
        column = classes.index(category)
        candidates = []
        for index, photo in enumerate(photos):
            score = float(probabilities[index, column])
            record = store.get(photo) if store else None
            if record is not None:
                # A confirmed photo outranks anything the model guessed, and a
                # photo confirmed as something else drops out of this column.
                score = 1.0 if record.category == category else 0.0
            candidates.append(Candidate(photo, score, confirmed=record is not None))
        candidates.sort(key=lambda c: -c.score)
        prediction.ranked[category] = candidates

    for index, photo in enumerate(photos):
        record = store.get(photo) if store else None
        if record is not None and record.category:
            prediction.best_guess[photo] = record.category
        else:
            prediction.best_guess[photo] = classes[int(np.argmax(probabilities[index]))]

    return prediction


def predict_site(
    site_dir: Path, model, store: ConfirmationStore | None = None,
) -> dict[str, SectorPrediction]:
    """Predict every sector folder (S1, S2, ...) under a site folder."""
    sectors = sorted(
        d for d in site_dir.iterdir() if d.is_dir() and not d.name.startswith(".")
    )
    return {d.name: predict_sector(d, model, store) for d in sectors}
