"""Does this photo actually show the instrument the slot calls for?

The category classifier ranks photos by overall resemblance, which is enough to
order candidates but not enough to be right about *what is in the frame*. It put
a cable-tag close-up under "Antenna M Tilt" because the colours and layout were
close, and a tilt photo under "Azimuth" for the same reason.

So the two instrument slots get a gate on top of the ranking, stating the rule
plainly:

* **Azimuth** must show a **compass** — a round dial.
* **Mechanical tilt** must show a **meter displaying a reading** — a panel with
  digits on it.

These checks are **advisory**, and that is a deliberate correction. They started
as hard gates that refused to place a photo failing them, with thresholds picked
by eye on synthetic test images. Measured against 157 photos the engineers had
actually placed, the strict version accepted 15% of real compasses and **none**
of the real meters; loosening it far enough to admit the real ones made it
accept nearly everything. Neither is a usable gate.

What the evidence is good for is *ranking* and *explaining*. The same signals now
feed the classifier as features (see `features.py`), where they are weighed
against everything else and trained on real Post photos, which lifted the
mechanical-tilt pick from 2 sectors in 4 to 22 in 25. These functions remain as
the human-readable half: they say why a photo looks like a compass or a display,
and that reason is shown in the review screen.

The detectors are deliberately colour-agnostic. The survey crew changed
instruments between rounds — the Pre photos show a green Digi-Pas inclinometer,
the Post photos a blue angle gauge — so anything keyed to colour would pass one
round and fail the other. Shape and content survive the change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

# Analysis size: big enough for a dial rim and LCD digits to survive.
WORK_LONG_EDGE = 640

# A dial is found with a Hough circle transform rather than by contour
# roundness. On a real compass the rim is broken up by tick marks, bearing
# numbers and a knurled bezel, and the background is usually foliage, so the
# external contour is never close to a circle — measured against 34 photos the
# engineers actually placed, contour roundness recognised 5 of them. Hough votes
# on arcs instead and does not care that the outline is interrupted.
DIAL_RADIUS_MIN_FRACTION = 0.13     # of the frame's short edge
DIAL_RADIUS_MAX_FRACTION = 0.62

# A reading is a row of similar glyphs on a display. Both polarities have to be
# accepted: the survey has used a grey LCD with dark digits *and* a SHAHE
# inclinometer with a dark LCD and bright green digits. Assuming dark-on-light
# recognised 0 of the 31 photos the engineers actually placed.
READING_MIN_DIGITS = 2
GLYPH_AREA_MIN = 0.00012            # of the frame
GLYPH_AREA_MAX = 0.06
# Digits in a reading share a height and sit on a line. Photos are often taken
# rotated, so alignment is checked along the row's own axis, not the frame's.
GLYPH_HEIGHT_TOLERANCE = 0.42


@dataclass
class SubjectCheck:
    """Whether a photo shows the required subject, and why."""

    passed: bool
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)


def _load_gray_hsv(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with Image.open(path) as img:
        img.load()
        oriented = ImageOps.exif_transpose(img).convert("RGB")
        w, h = oriented.size
        scale = WORK_LONG_EDGE / max(w, h)
        if scale < 1.0:
            oriented = oriented.resize(
                (max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS
            )
        rgb = np.asarray(oriented)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)


def has_compass_dial(path: Path) -> SubjectCheck:
    """True when a large round dial is in frame.

    Uses a Hough circle transform: a compass rim is interrupted by tick marks,
    numbers and a knurled bezel, so it is never a clean contour, but it does vote
    strongly as a circle. Antenna hardware in these photos is rectilinear and the
    tilt scale is a straight strip, so a circle this large is a compass.
    """
    gray, _ = _load_gray_hsv(path)
    height, width = gray.shape[:2]
    short_edge = min(width, height)

    blurred = cv2.medianBlur(gray, 5)
    circles = cv2.HoughCircles(
        blurred, cv2.HOUGH_GRADIENT, dp=1.2,
        minDist=short_edge * 0.35,
        param1=110, param2=55,
        minRadius=int(short_edge * DIAL_RADIUS_MIN_FRACTION),
        maxRadius=int(short_edge * DIAL_RADIUS_MAX_FRACTION),
    )
    if circles is None:
        return SubjectCheck(
            passed=False, score=0.0, reasons=["no round compass dial in frame"]
        )

    best = max(circles[0], key=lambda c: c[2])
    radius = float(best[2])
    fraction = (np.pi * radius * radius) / float(width * height)
    return SubjectCheck(
        passed=True,
        score=float(min(0.45 + fraction * 2.0, 1.0)),
        reasons=[f"round dial found, filling {fraction:.0%} of the frame"],
    )


def digit_row_length(mask: np.ndarray, frame_area: float) -> int:
    """Count glyphs in the largest row of similar, aligned blobs in a mask."""
    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    glyphs = []
    for index in range(1, count):
        area = float(stats[index, cv2.CC_STAT_AREA])
        if not frame_area * GLYPH_AREA_MIN <= area <= frame_area * GLYPH_AREA_MAX:
            continue
        w = int(stats[index, cv2.CC_STAT_WIDTH])
        h = int(stats[index, cv2.CC_STAT_HEIGHT])
        if w < 3 or h < 5:
            continue
        if max(w, h) / max(min(w, h), 1) > 6.0:
            continue                       # a bar or a cable, not a digit
        glyphs.append((float(centroids[index][0]), float(centroids[index][1]),
                       max(w, h)))
    if len(glyphs) < READING_MIN_DIGITS:
        return 0

    # Group by similar size, then require them to lie on a line. Photos are
    # often rotated, so the line may run in any direction.
    best = 0
    for cx, cy, size in glyphs:
        peers = [g for g in glyphs
                 if abs(g[2] - size) <= size * GLYPH_HEIGHT_TOLERANCE]
        if len(peers) < READING_MIN_DIGITS:
            continue
        xs = np.array([g[0] for g in peers])
        ys = np.array([g[1] for g in peers])
        # Spread along the row's own axis must dominate spread across it.
        centred = np.vstack([xs - xs.mean(), ys - ys.mean()])
        if centred.shape[1] < 2:
            continue
        _, singular, _ = np.linalg.svd(centred, full_matrices=False)
        along, across = float(singular[0]), float(singular[1])
        if along > max(across, 1e-6) * 1.6 and along > size:
            best = max(best, len(peers))
    return best


def has_meter_reading(path: Path) -> SubjectCheck:
    """True when a display showing a value is in frame.

    Looks for a row of similar glyphs in *either* polarity — dark digits on a
    pale LCD, and bright digits on a dark LCD. The survey has used both, and
    assuming one of them recognised none of the photos the engineers placed.
    """
    gray, _ = _load_gray_hsv(path)
    frame_area = float(gray.size)
    equalised = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)

    dark_on_light = cv2.adaptiveThreshold(
        equalised, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 12
    )
    light_on_dark = cv2.adaptiveThreshold(
        equalised, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 31, 12
    )
    kernel = np.ones((2, 2), np.uint8)

    # Both polarities are tried and the stronger row wins. Which one produced it
    # is deliberately not reported: on a dark LCD the gaps between bright digits
    # also read as a row, so naming the polarity would be a guess.
    best_digits = 0
    for mask in (dark_on_light, light_on_dark):
        cleaned = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        best_digits = max(best_digits, digit_row_length(cleaned, frame_area))

    if best_digits >= READING_MIN_DIGITS:
        return SubjectCheck(
            passed=True,
            score=float(min(0.4 + 0.15 * best_digits, 1.0)),
            reasons=[f"display showing {best_digits} digits"],
        )
    return SubjectCheck(
        passed=False, score=0.0,
        reasons=["no meter display with a readable value in frame"],
    )


# Slot category -> the gate its photo must pass.
GATES = {
    "azimuth": has_compass_dial,
    "mechanical_tilt": has_meter_reading,
}


def check(category: str, path: Path) -> SubjectCheck:
    """Run the gate for this category, or pass when the category has none."""
    gate = GATES.get(category)
    if gate is None:
        return SubjectCheck(passed=True, score=1.0)
    try:
        return gate(path)
    except Exception as exc:  # noqa: BLE001 - a bad file must not stop a run
        return SubjectCheck(passed=False, score=0.0,
                            reasons=[f"could not read the photo ({exc})"])
