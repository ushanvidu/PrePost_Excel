"""Train the category classifier on the already-labelled Pre photos.

Two details matter for an honest accuracy number.

*De-duplication.* The survey files a single photograph under more than one band
whenever bands share an array — 850 and 900 always, sometimes more. Fifteen of
the Pre tilt files are byte-identical copies of another. Left in, they inflate
cross-validation by putting the same image in both the train and test fold, so
identical files are collapsed to one row before fitting.

*Grouping.* Photos from one site share lighting, antenna, and background. A
random split would let the model recognise the site rather than the subject, so
cross-validation splits by site: every fold tests on sites the model never saw.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .. import catalog
from . import labels
from .features import describe

MODEL_FILENAME = "category_model.joblib"


@dataclass
class Dataset:
    paths: list[Path] = field(default_factory=list)
    y: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)   # site, for grouped CV
    duplicates_dropped: int = 0
    confirmed_used: int = 0

    def __len__(self) -> int:
        return len(self.paths)


def build_dataset(
    images_root: Path, confirmed: list[tuple[Path, str, str]] | None = None
) -> Dataset:
    """Collect labelled Pre photos plus any user-confirmed Post photos.

    Confirmed photos matter more than their count suggests: they are the only
    in-domain examples of the Post survey's instruments, and adding them is what
    lifts single-slot accuracy from half-right to fully right.
    """
    dataset = Dataset()
    seen: dict[str, Path] = {}

    for path, category, group in confirmed or []:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        if digest in seen:
            continue
        seen[digest] = path
        dataset.paths.append(path)
        dataset.y.append(category)
        dataset.groups.append(group)
    dataset.confirmed_used = len(dataset.paths)

    for inventory in catalog.scan_root(images_root):
        for photo in inventory.photos:
            digest = hashlib.sha256(photo.path.read_bytes()).hexdigest()
            if digest in seen:
                dataset.duplicates_dropped += 1
                continue
            seen[digest] = photo.path
            dataset.paths.append(photo.path)
            dataset.y.append(labels.visual_class(photo.category))
            dataset.groups.append(inventory.site)

    return dataset


def make_model() -> Pipeline:
    """Scale, then a linear model. Small data, so keep the hypothesis simple."""
    return Pipeline([
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(
            max_iter=4000,
            C=1.0,
            class_weight="balanced",   # 'other' vastly outnumbers the rest
        )),
    ])


def evaluate(X: np.ndarray, dataset: Dataset) -> dict:
    """Leave-sites-out cross-validation, reported per class."""
    n_groups = len(set(dataset.groups))
    splits = min(5, n_groups)
    predicted = cross_val_predict(
        make_model(), X, dataset.y,
        groups=dataset.groups, cv=GroupKFold(n_splits=splits),
    )
    truth = np.asarray(dataset.y)
    report = {}
    for name in labels.CATEGORIES:
        is_class = truth == name
        got_class = predicted == name
        support = int(is_class.sum())
        if not support:
            continue
        tp = int((is_class & got_class).sum())
        report[name] = {
            "support": support,
            "recall": tp / support,
            "precision": tp / max(int(got_class.sum()), 1),
        }
    return {
        "overall_accuracy": float((predicted == truth).mean()),
        "folds": splits,
        "per_class": report,
        "confusions": Counter(
            (t, p) for t, p in zip(truth, predicted) if t != p
        ).most_common(8),
    }


def train(
    images_root: Path, out_dir: Path,
    confirmed: list[tuple[Path, str, str]] | None = None,
) -> dict:
    """Build the dataset, score it honestly, fit on everything, and save."""
    import joblib

    dataset = build_dataset(images_root, confirmed)
    if not dataset:
        raise ValueError(f"No labelled photos found under {images_root}")

    X = np.vstack([describe(p) for p in dataset.paths])
    scores = evaluate(X, dataset)

    model = make_model().fit(X, dataset.y)
    out_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "categories": labels.CATEGORIES}, out_dir / MODEL_FILENAME)

    scores["n_images"] = len(dataset)
    scores["duplicates_dropped"] = dataset.duplicates_dropped
    scores["class_counts"] = dict(Counter(dataset.y))
    scores["confirmed_used"] = dataset.confirmed_used
    scores["model_path"] = str(out_dir / MODEL_FILENAME)
    return scores
