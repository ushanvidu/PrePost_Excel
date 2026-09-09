"""Port detection, band assignment, and the review routes."""

import numpy as np
import pytest
from PIL import Image, ImageDraw

from antenna_audit.classify import labels, ports
from antenna_audit.classify.bands import (
    HIGH_BAND_ROWS,
    LOW_BAND_ROWS,
    assign_sector,
    to_post_photos,
)
from antenna_audit.classify.features import describe
from antenna_audit.classify.store import ConfirmationStore
from antenna_audit.classify.train import make_model
from antenna_audit.web.server import create_app


def _plain(path, colour=(150, 150, 150), size=(400, 300)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path, quality=92)
    return path


def _with_ring(path, size=(400, 300), radius=34, colour=(0, 0, 235)):
    """A grey frame with a saturated red annulus, like the painted connector."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, (150, 150, 150))
    draw = ImageDraw.Draw(image)
    cx, cy = size[0] // 2, size[1] // 2
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                 outline=(230, 8, 8), width=9)
    draw.ellipse((cx - 14, cy - 14, cx + 14, cy + 14), fill=(25, 25, 25))  # connector
    image.save(path, quality=95)
    return path


# --- antenna profiles -------------------------------------------------------

def test_profile_normalises_ocr_style_text():
    assert ports.AQU4518.normalise("(RB Y3)") == "RBy3"
    assert ports.AQU4518.normalise("rty2") == "RTy2"
    assert ports.AQU4518.normalise("R1") == "r1"
    assert ports.AQU4518.normalise("nonsense") is None


def test_the_low_band_port_is_one_of_the_profile_ports():
    for profile in ports.PROFILES.values():
        assert profile.low_band_port in profile.ports


# --- red ring detector ------------------------------------------------------

def test_ring_is_found_on_a_synthetic_annulus(tmp_path):
    rings = ports.find_red_rings(_with_ring(tmp_path / "ring.jpg"))
    assert rings, "a painted annulus should be detected"
    assert rings[0].score > 0.4


def test_no_ring_on_a_plain_frame(tmp_path):
    assert ports.find_red_rings(_plain(tmp_path / "plain.jpg")) == []


def test_rust_coloured_frame_does_not_count_as_paint(tmp_path):
    """The whole point of the saturation floor: corrosion is not a ring."""
    rust = _plain(tmp_path / "rust.jpg", colour=(120, 68, 44))
    assert ports.find_red_rings(rust) == []


def test_analyse_reports_band_group_and_reasons(tmp_path):
    ringed = ports.analyse(_with_ring(tmp_path / "a.jpg"))
    assert ringed.band_group == "low"
    assert ringed.port == ports.AQU4518.low_band_port
    assert ringed.reasons and ringed.confidence > 0.5

    plain = ports.analyse(_plain(tmp_path / "b.jpg"))
    assert plain.band_group == "high"
    assert plain.port is None


# --- band assignment --------------------------------------------------------

@pytest.fixture
def model(tmp_path):
    paths, y = [], []
    for i in range(4):
        paths.append(_plain(tmp_path / "t" / f"az{i}.jpg", (20 + i, 190, 60)))
        y.append(labels.AZIMUTH)
        paths.append(_plain(tmp_path / "t" / f"cov{i}.jpg", (120 + i, 170, 230)))
        y.append(labels.COVERAGE)
        paths.append(_plain(tmp_path / "t" / f"me{i}.jpg", (10, 10, 200 + i)))
        y.append(labels.MECHANICAL_TILT)
        paths.append(_with_ring(tmp_path / "t" / f"et{i}.jpg", radius=30 + i))
        y.append(labels.ELECTRICAL_TILT)
    return make_model().fit(np.vstack([describe(p) for p in paths]), y)


def test_850_and_900_receive_the_same_photograph(tmp_path, model):
    """They share one array and one adjuster, so there is one photo to place."""
    sector = tmp_path / "S1"
    for i in range(3):
        _with_ring(sector / f"tilt{i}.jpg", radius=30 + i)
    assignment = assign_sector(sector, model)

    first, second = (assignment.slots[r] for r in LOW_BAND_ROWS)
    assert first.filled and second.filled
    assert first.photo == second.photo


def test_high_bands_are_never_guessed(tmp_path, model):
    """A wrong photo under a band heading is worse than an empty slot."""
    sector = tmp_path / "S1"
    for i in range(4):
        _with_ring(sector / f"tilt{i}.jpg", radius=28 + i)
    assignment = assign_sector(sector, model)

    for row in HIGH_BAND_ROWS:
        slot = assignment.slots[row]
        assert not slot.filled
        assert slot.needs_decision
        assert any("cabling" in r for r in slot.reasons)


def test_a_confirmed_band_fills_its_slot(tmp_path, model):
    sector = tmp_path / "S1"
    photos = [_with_ring(sector / f"tilt{i}.jpg", radius=28 + i) for i in range(3)]

    store = ConfirmationStore(tmp_path / "models")
    store.add(photos[1], "SITE", "S1", labels.ELECTRICAL_TILT, port="2100_Tilt")

    assignment = assign_sector(sector, model, store)
    slot = assignment.slots["2100_Tilt"]
    assert slot.filled and slot.confirmed
    assert slot.photo == photos[1]
    assert not slot.needs_decision


def test_a_slot_is_never_filled_twice_over(tmp_path, model):
    """One photo must not end up in two different slots."""
    sector = tmp_path / "S1"
    for i in range(6):
        _plain(sector / f"p{i}.jpg", (20 + i * 3, 190, 60))
    assignment = assign_sector(sector, model)
    filled = [s.photo for s in assignment.slots.values() if s.filled]
    # 850 and 900 legitimately share one photo; everything else is distinct.
    assert len(filled) - len(set(filled)) <= 1


def test_to_post_photos_keys_by_sector_number(tmp_path, model):
    sector = tmp_path / "S3"
    for i in range(3):
        _with_ring(sector / f"tilt{i}.jpg", radius=30 + i)
    mapping = to_post_photos({"S3": assign_sector(sector, model)})
    assert all(key[0] == 3 for key in mapping)
    assert (3, "850_Tilt") in mapping


def test_unresolved_slots_stay_out_of_the_mapping(tmp_path, model):
    sector = tmp_path / "S1"
    for i in range(3):
        _with_ring(sector / f"tilt{i}.jpg", radius=30 + i)
    mapping = to_post_photos({"S1": assign_sector(sector, model)})
    for row in HIGH_BAND_ROWS:
        assert (1, row) not in mapping


# --- review routes ----------------------------------------------------------

@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    app.extensions["job_store"].shutdown()


def test_review_page_loads(client):
    response = client.get("/review")
    assert response.status_code == 200
    assert b"Review Post photos" in response.data


def test_scan_rejects_a_bad_folder(client):
    assert client.post("/api/review/scan", json={"postPath": ""}).status_code == 400
    assert client.post(
        "/api/review/scan", json={"postPath": "/no/such/folder"}
    ).status_code == 400


def test_thumbnail_refuses_paths_outside_the_scanned_folder(client):
    """The route takes a path, so it must serve nothing the user didn't open."""
    for attack in ("/etc/passwd", "/etc/hosts"):
        assert client.get(f"/api/review/thumb?path={attack}").status_code == 404
    assert client.get("/api/review/thumb").status_code == 400


def test_confirm_rejects_an_unknown_slot(client, tmp_path):
    photo = _plain(tmp_path / "x.jpg")
    response = client.post("/api/review/confirm", json={
        "path": str(photo), "row": "not_a_real_row",
        "site": "S", "sector": "S1", "modelDir": str(tmp_path),
    })
    assert response.status_code == 400


def test_a_weak_best_guess_is_offered_not_placed(tmp_path, model):
    """Below the floor the photo becomes a candidate, never a silent placement.

    On the measured set every confident pick was right and every mistake came in
    below this line, so deferring the weak ones turned three wrong placements
    into three questions.
    """
    from antenna_audit.classify.bands import MIN_AUTOFILL_SCORE

    sector = tmp_path / "S1"
    # Nothing here resembles a compass, so azimuth should score poorly.
    for i in range(4):
        _plain(sector / f"p{i}.jpg", (128, 128, 128 + i))
    assignment = assign_sector(sector, model)

    slot = assignment.slots["Antenna_Azimuth_Photo"]
    if slot.confidence < MIN_AUTOFILL_SCORE:
        assert not slot.filled, "a weak guess must not be written into the sheet"
        assert slot.needs_decision
        assert slot.alternatives, "but it must still be offered as a candidate"


# --- API errors must never be HTML ------------------------------------------
# The browser reads these replies with res.json(). Flask's default error page is
# HTML, which surfaced in the console as `Unexpected token '<', "<!doctype "...`
# and told the user nothing. Every /api/* reply is JSON now, whatever happens.

def test_unknown_api_route_answers_in_json(client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.mimetype == "application/json"
    assert "error" in response.get_json()


def test_pages_are_still_html(client):
    """Only /api/* is forced to JSON; the pages themselves must still render."""
    for path in ("/", "/review"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.mimetype == "text/html"


def test_a_crash_inside_an_api_route_answers_in_json(client, monkeypatch):
    """Even an unexpected exception comes back as JSON with the reason."""
    import antenna_audit.web.review as review

    def boom(*args, **kwargs):
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(review, "ConfirmationStore", boom)
    response = client.post("/api/review/confirm", json={
        "path": "/tmp/x.jpg", "row": "850_Tilt", "site": "S", "sector": "S1",
    })
    assert response.mimetype == "application/json"
    assert "error" in response.get_json()


def test_training_on_the_wrong_folder_explains_itself(client, tmp_path):
    """The commonest mistake: pointing at the Post photos instead of the Pre."""
    empty = tmp_path / "not-the-pre-photos"
    (empty / "S1").mkdir(parents=True)
    models = tmp_path / "fresh-models"        # no trained model, as on a clone
    site = tmp_path / "site"
    (site / "S1").mkdir(parents=True)

    response = client.post("/api/review/scan", json={
        "postPath": str(site), "imagesRoot": str(empty), "modelDir": str(models),
    })
    assert response.status_code == 400
    assert response.mimetype == "application/json"
    message = response.get_json()["error"]
    assert "Pre photos" in message and "Ant_Sec_1" in message
