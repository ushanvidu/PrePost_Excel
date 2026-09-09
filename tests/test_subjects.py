"""Instrument evidence: does a photo look like a compass, or like a display?

These checks are advisory. They began as hard gates that refused to place a
photo failing them, and measurement killed that idea: against 157 photos the
engineers had actually placed, the strict version accepted 15% of real compasses
and none of the real meters, while a version loose enough to admit the real ones
accepted nearly everything.

So the tests below assert what the module now claims — that it recognises each
instrument and says what it found — and not that it rejects everything else.
Discrimination is the classifier's job, measured on real photos in
`docs/accuracy.md`, not a threshold asserted here.
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from antenna_audit.classify import labels
from antenna_audit.classify.subjects import (
    check,
    digit_row_length,
    has_compass_dial,
    has_meter_reading,
)


def _compass(path: Path, size=(500, 500), radius=150) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, (60, 90, 55))
    draw = ImageDraw.Draw(image)
    cx, cy = size[0] // 2, size[1] // 2
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                 fill=(240, 240, 235), outline=(20, 20, 20), width=6)
    draw.line((cx, cy - radius + 20, cx, cy + radius - 20), fill=(200, 30, 30), width=6)
    image.save(path, quality=95)
    return path


def _display(path: Path, dark_lcd: bool, size=(500, 400), digits=3) -> Path:
    """A meter display in either polarity.

    The survey has used both: a grey LCD with dark digits, and a SHAHE
    inclinometer with a dark LCD and bright green digits.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (35, 60, 130)
    panel = (30, 34, 30) if dark_lcd else (225, 228, 220)
    glyph = (90, 240, 110) if dark_lcd else (25, 25, 25)
    image = Image.new("RGB", size, body)
    draw = ImageDraw.Draw(image)
    draw.rectangle((110, 130, 390, 260), fill=panel)
    for i in range(digits):
        x = 140 + i * 75
        draw.rectangle((x, 165, x + 45, 230), fill=glyph)
    image.save(path, quality=95)
    return path


def _flat(path: Path, size=(400, 300)) -> Path:
    """No dial, no display, no texture at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (150, 148, 140)).save(path, quality=95)
    return path


# --- compass ----------------------------------------------------------------

def test_a_dial_is_recognised_and_described(tmp_path):
    result = has_compass_dial(_compass(tmp_path / "compass.jpg"))
    assert result.passed
    assert 0 < result.score <= 1
    assert "dial" in " ".join(result.reasons)


def test_a_featureless_frame_has_no_dial(tmp_path):
    result = has_compass_dial(_flat(tmp_path / "flat.jpg"))
    assert not result.passed
    assert "no round compass dial" in " ".join(result.reasons)


def test_dial_score_rises_with_dial_size(tmp_path):
    """The score is a ranking signal, so a bigger dial must score higher."""
    small = has_compass_dial(_compass(tmp_path / "small.jpg", radius=80))
    large = has_compass_dial(_compass(tmp_path / "large.jpg", radius=200))
    assert large.score > small.score


# --- display ----------------------------------------------------------------

def test_dark_digits_on_a_pale_display_are_read(tmp_path):
    result = has_meter_reading(_display(tmp_path / "pale.jpg", dark_lcd=False))
    assert result.passed
    assert "digits" in " ".join(result.reasons)


def test_bright_digits_on_a_dark_display_are_read(tmp_path):
    """The polarity that a dark-on-light-only detector missed entirely."""
    result = has_meter_reading(_display(tmp_path / "dark.jpg", dark_lcd=True))
    assert result.passed
    assert "digits" in " ".join(result.reasons)


def test_both_polarities_report_the_digit_count(tmp_path):
    """Which polarity produced the row is not reported: on a dark LCD the gaps
    between bright digits also read as a row, so naming it would be a guess."""
    for dark_lcd in (True, False):
        result = has_meter_reading(_display(tmp_path / f"{dark_lcd}.jpg",
                                            dark_lcd=dark_lcd, digits=3))
        assert result.passed
        assert "3 digits" in " ".join(result.reasons)


def test_a_featureless_frame_has_no_reading(tmp_path):
    result = has_meter_reading(_flat(tmp_path / "flat.jpg"))
    assert not result.passed


# --- the digit-row helper ---------------------------------------------------

def test_digit_row_needs_a_row(tmp_path):
    """Scattered blobs are not a reading; blobs on a line are."""
    frame = 400 * 300

    empty = np.zeros((300, 400), np.uint8)
    assert digit_row_length(empty, frame) == 0

    row = np.zeros((300, 400), np.uint8)
    for i in range(4):
        row[140:180, 60 + i * 60: 100 + i * 60] = 255
    assert digit_row_length(row, frame) >= 2


# --- dispatch ---------------------------------------------------------------

def test_check_routes_to_the_right_detector(tmp_path):
    compass = _compass(tmp_path / "c.jpg")
    display = _display(tmp_path / "m.jpg", dark_lcd=False)
    assert check(labels.AZIMUTH, compass).passed
    assert check(labels.MECHANICAL_TILT, display).passed


def test_categories_without_a_check_always_pass(tmp_path):
    """Only the two instrument slots have evidence to gather."""
    flat = _flat(tmp_path / "x.jpg")
    assert check(labels.COVERAGE, flat).passed
    assert check(labels.ELECTRICAL_TILT, flat).passed


def test_an_unreadable_file_fails_closed_with_a_reason(tmp_path):
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    result = check(labels.AZIMUTH, broken)
    assert not result.passed
    assert result.reasons
