"""The category classifier, the confirmation store, and the loop between them."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from antenna_audit.classify import labels
from antenna_audit.classify.features import describe
from antenna_audit.classify.predict import SINGLE_SLOT, predict_sector, sector_photos
from antenna_audit.classify.store import ConfirmationStore, digest_of
from antenna_audit.classify.train import build_dataset, make_model


def _photo(path: Path, colour=(120, 120, 120), size=(320, 240), circle=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, colour)
    if circle:
        draw = ImageDraw.Draw(image)
        draw.ellipse((60, 40, 220, 200), fill=(30, 200, 60))
    image.save(path, quality=92)
    return path


# --- features ---------------------------------------------------------------

def test_feature_vector_is_fixed_length_and_finite(tmp_path):
    a = describe(_photo(tmp_path / "a.jpg", (200, 40, 40)))
    b = describe(_photo(tmp_path / "b.jpg", (10, 10, 200), size=(240, 320)))
    assert a.shape == b.shape
    assert a.size > 50
    assert np.isfinite(a).all() and np.isfinite(b).all()


def test_features_are_deterministic(tmp_path):
    path = _photo(tmp_path / "same.jpg", (90, 160, 60))
    assert np.array_equal(describe(path), describe(path))


def test_features_separate_visibly_different_images(tmp_path):
    flat = describe(_photo(tmp_path / "flat.jpg", (250, 250, 250)))
    disc = describe(_photo(tmp_path / "disc.jpg", (250, 250, 250), circle=True))
    assert not np.allclose(flat, disc)


# --- label mapping ----------------------------------------------------------

def test_every_tilt_band_maps_to_one_visual_class():
    """Band is not a visual property; all five bands are the same kind of photo."""
    for band in ("850_Tilt", "900_Tilt", "1800_Tilt_1", "1800_Tilt_2", "2100_Tilt"):
        assert labels.visual_class(band) == labels.ELECTRICAL_TILT


def test_unrecognised_categories_fall_into_other():
    assert labels.visual_class("RRU_1_Lable_Photo") == labels.OTHER
    assert labels.visual_class("something_new") == labels.OTHER


# --- confirmation store -----------------------------------------------------

def test_store_round_trips(tmp_path):
    photo = _photo(tmp_path / "S1" / "shot.jpg")
    store = ConfirmationStore(tmp_path)
    assert store.get(photo) is None

    store.add(photo, site="GMNIT1", sector="S1", category=labels.AZIMUTH)
    store.save()

    reloaded = ConfirmationStore(tmp_path)
    record = reloaded.get(photo)
    assert record is not None
    assert record.category == labels.AZIMUTH
    assert record.site == "GMNIT1"


def test_store_recognises_the_same_photo_under_a_new_name(tmp_path):
    """WhatsApp renames on re-send; a confirmation must survive that."""
    original = _photo(tmp_path / "a" / "first.jpg", (40, 90, 160))
    store = ConfirmationStore(tmp_path)
    store.add(original, "GMNIT1", "S1", labels.COVERAGE)

    copy = tmp_path / "b" / "renamed.jpg"
    copy.parent.mkdir()
    copy.write_bytes(original.read_bytes())

    assert digest_of(copy) == digest_of(original)
    assert store.get(copy).category == labels.COVERAGE


def test_training_rows_skip_photos_that_are_gone(tmp_path):
    photo = _photo(tmp_path / "imgs" / "x.jpg")
    store = ConfirmationStore(tmp_path)
    store.add(photo, "S", "S1", labels.AZIMUTH)
    assert len(store.training_rows([tmp_path / "imgs"])) == 1

    photo.unlink()
    assert store.training_rows([tmp_path / "imgs"]) == []


# --- prediction -------------------------------------------------------------

@pytest.fixture
def tiny_model(tmp_path):
    """A model fitted on a few synthetic images, enough to exercise the API."""
    paths, y = [], []
    for i in range(4):
        # Vary each image slightly: identical pixels mean identical files, and
        # the store is keyed by content, which would make them one photo.
        paths.append(_photo(tmp_path / "train" / f"az{i}.jpg",
                            (20 + i, 190, 60), circle=True))
        y.append(labels.AZIMUTH)
        paths.append(_photo(tmp_path / "train" / f"cov{i}.jpg", (120 + i, 170, 230)))
        y.append(labels.COVERAGE)
        paths.append(_photo(tmp_path / "train" / f"mech{i}.jpg", (10, 10, 200 + i)))
        y.append(labels.MECHANICAL_TILT)
        paths.append(_photo(tmp_path / "train" / f"oth{i}.jpg", (60 + i, 60, 60)))
        y.append(labels.OTHER)
    X = np.vstack([describe(p) for p in paths])
    return make_model().fit(X, y)


def test_prediction_ranks_every_photo_for_every_slot(tmp_path, tiny_model):
    sector = tmp_path / "S1"
    for i in range(3):
        _photo(sector / f"p{i}.jpg", (20, 190, 60), circle=True)
    prediction = predict_sector(sector, tiny_model)

    assert len(prediction.photos) == 3
    for category in SINGLE_SLOT:
        ranked = prediction.ranked.get(category, [])
        assert len(ranked) == 3
        scores = [c.score for c in ranked]
        assert scores == sorted(scores, reverse=True), "candidates must be ranked"


def test_a_confirmation_outranks_the_model(tmp_path, tiny_model):
    """Once you have confirmed a photo, the model may not overrule you."""
    sector = tmp_path / "S1"
    for i in range(3):
        # All look like coverage, but each is a distinct file.
        _photo(sector / f"p{i}.jpg", (120, 170, 230 - i))
    photos = sector_photos(sector)
    underdog = photos[-1]

    store = ConfirmationStore(tmp_path)
    store.add(underdog, "SITE", "S1", labels.AZIMUTH)

    prediction = predict_sector(sector, tiny_model, store)
    top = prediction.ranked[labels.AZIMUTH][0]
    assert top.path == underdog
    assert top.confirmed
    assert prediction.best_guess[underdog] == labels.AZIMUTH

    # ...and it is removed from the slot it is *not*.
    coverage = {c.path: c.score for c in prediction.ranked[labels.COVERAGE]}
    assert coverage[underdog] == 0.0


def test_empty_sector_is_handled(tmp_path, tiny_model):
    sector = tmp_path / "S9"
    sector.mkdir()
    prediction = predict_sector(sector, tiny_model)
    assert prediction.photos == []
    assert prediction.electrical_tilt_photos() == []


# --- dataset ----------------------------------------------------------------

def test_confirmed_photos_join_the_training_set(tmp_path):
    """Confirmations are the only in-domain examples of the Post instruments."""
    images_root = tmp_path / "pre"
    site = images_root / "SITE01"
    prefix = "Sector_1_RF_Antenna_Photos_Sector_1_RF_Antenna_Photos_Ant_Sec_1_"
    _photo(site / f"{prefix}_850_Tilt_1.jpg")
    _photo(site / f"{prefix}Antenna_M_Tilt_1.jpg", (30, 30, 30))

    extra = _photo(tmp_path / "post" / "confirmed.jpg", (10, 10, 200))
    dataset = build_dataset(
        images_root, confirmed=[(extra, labels.MECHANICAL_TILT, "GMNIT1")]
    )

    assert dataset.confirmed_used == 1
    assert extra in dataset.paths
    assert len(dataset) == 3


def test_duplicate_files_are_collapsed(tmp_path):
    """850 and 900 are the same photograph; counting it twice inflates scores."""
    images_root = tmp_path / "pre"
    site = images_root / "SITE01"
    prefix = "Sector_1_RF_Antenna_Photos_Sector_1_RF_Antenna_Photos_Ant_Sec_1_"
    first = _photo(site / f"{prefix}_850_Tilt_1.jpg")
    (site / f"{prefix}_900_Tilt_1.jpg").write_bytes(first.read_bytes())

    dataset = build_dataset(images_root)
    assert len(dataset) == 1
    assert dataset.duplicates_dropped == 1
