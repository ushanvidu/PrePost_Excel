"""The subject gates: azimuth needs a compass, mechanical tilt needs a reading."""

from pathlib import Path

from PIL import Image, ImageDraw

from antenna_audit.classify import labels
from antenna_audit.classify.subjects import (
    check,
    has_compass_dial,
    has_meter_reading,
)


def _compass(path: Path, size=(500, 500), radius=150) -> Path:
    """A large round dial, as a compass held up to the camera looks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, (60, 90, 55))
    draw = ImageDraw.Draw(image)
    cx, cy = size[0] // 2, size[1] // 2
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                 fill=(240, 240, 235), outline=(20, 20, 20), width=6)
    draw.line((cx, cy - radius + 20, cx, cy + radius - 20), fill=(200, 30, 30), width=6)
    image.save(path, quality=95)
    return path


def _meter(path: Path, size=(500, 400), digits=3) -> Path:
    """A dark instrument with a pale panel carrying dark digits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, (35, 60, 130))
    draw = ImageDraw.Draw(image)
    draw.rectangle((110, 130, 390, 260), fill=(225, 228, 220))     # the display
    for i in range(digits):                                        # the reading
        x = 140 + i * 75
        draw.rectangle((x, 165, x + 45, 230), fill=(25, 25, 25))
    image.save(path, quality=95)
    return path


def _blank_meter(path: Path, size=(500, 400)) -> Path:
    """The same instrument with nothing on the display."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, (35, 60, 130))
    ImageDraw.Draw(image).rectangle((110, 130, 390, 260), fill=(225, 228, 220))
    image.save(path, quality=95)
    return path


def _clutter(path: Path, size=(500, 400)) -> Path:
    """Antenna hardware: rectilinear, no dial, no display."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, (150, 148, 140))
    draw = ImageDraw.Draw(image)
    for i in range(5):
        draw.rectangle((40 + i * 90, 200, 90 + i * 90, 380), fill=(30, 30, 30))
    image.save(path, quality=95)
    return path


# --- azimuth: only a compass counts -----------------------------------------

def test_compass_passes_the_azimuth_gate(tmp_path):
    result = has_compass_dial(_compass(tmp_path / "compass.jpg"))
    assert result.passed
    assert result.score > 0
    assert "dial" in " ".join(result.reasons)


def test_antenna_hardware_fails_the_azimuth_gate(tmp_path):
    """The failure that prompted this: a tilt photo ranked top for azimuth."""
    result = has_compass_dial(_clutter(tmp_path / "hardware.jpg"))
    assert not result.passed
    assert "no round compass dial" in " ".join(result.reasons)


def test_a_meter_is_not_a_compass(tmp_path):
    assert not has_compass_dial(_meter(tmp_path / "meter.jpg")).passed


# --- mechanical tilt: only a meter showing a reading ------------------------

def test_meter_with_digits_passes(tmp_path):
    result = has_meter_reading(_meter(tmp_path / "meter.jpg"))
    assert result.passed
    assert "digits" in " ".join(result.reasons)


def test_meter_with_a_blank_display_fails(tmp_path):
    """'The meter which shows a reading' — a blank display is not a reading."""
    assert not has_meter_reading(_blank_meter(tmp_path / "blank.jpg")).passed


def test_antenna_hardware_fails_the_meter_gate(tmp_path):
    assert not has_meter_reading(_clutter(tmp_path / "hardware.jpg")).passed


def test_a_compass_is_not_a_meter(tmp_path):
    assert not has_meter_reading(_compass(tmp_path / "compass.jpg")).passed


# --- dispatch ---------------------------------------------------------------

def test_check_routes_to_the_right_gate(tmp_path):
    compass = _compass(tmp_path / "c.jpg")
    meter = _meter(tmp_path / "m.jpg")
    assert check(labels.AZIMUTH, compass).passed
    assert not check(labels.AZIMUTH, meter).passed
    assert check(labels.MECHANICAL_TILT, meter).passed
    assert not check(labels.MECHANICAL_TILT, compass).passed


def test_categories_without_a_gate_always_pass(tmp_path):
    """Coverage and electrical tilt are not gated; only the two instruments are."""
    clutter = _clutter(tmp_path / "x.jpg")
    assert check(labels.COVERAGE, clutter).passed
    assert check(labels.ELECTRICAL_TILT, clutter).passed


def test_an_unreadable_file_fails_closed(tmp_path):
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    result = check(labels.AZIMUTH, broken)
    assert not result.passed
    assert result.reasons
