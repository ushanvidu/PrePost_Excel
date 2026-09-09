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

A photo that fails its gate is not placed, however well it ranked. The slot is
offered for review instead, because an empty slot with a reason beats a
confident wrong answer in an audit document.

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

# A dial must be at least this round (1.0 is a perfect circle) and occupy at
# least this share of the frame before it counts as a compass.
DIAL_CIRCULARITY_MIN = 0.62
DIAL_AREA_MIN = 0.012

# A reading needs a bright panel holding this many separate dark glyphs.
READING_MIN_DIGITS = 2
PANEL_AREA_MIN = 0.006
# A display is part of an instrument, never the whole frame. Without an upper
# bound a pale background counts as one enormous panel and the dark hardware in
# front of it counts as digits.
PANEL_AREA_MAX = 0.45
# A display is rectangular. A disc fills only pi/4 = 0.785 of its bounding box,
# so this floor is what stops a compass dial being read as a panel. It is
# measured on the panel's *outer* contour: the digits punch holes in the panel,
# and counting those holes as missing area rejected genuine displays.
PANEL_FILL_MIN = 0.82


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
    """True when a round dial fills a real part of the frame.

    A compass is held up to the camera, so its dial is large and close to a
    circle. Antenna hardware in these photos is rectilinear — connectors are
    small, and the tilt scale is a straight strip — so roundness at this size is
    a strong signal that separates a compass from everything else on the tower.
    """
    gray, hsv = _load_gray_hsv(path)
    frame_area = float(gray.size)

    edges = cv2.Canny(cv2.medianBlur(gray, 5), 40, 130)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best_score = 0.0
    best_area = 0.0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < frame_area * DIAL_AREA_MIN:
            continue
        perimeter = cv2.arcLength(contour, True)
        if perimeter <= 0:
            continue
        circularity = 4 * np.pi * area / (perimeter ** 2)
        if circularity < DIAL_CIRCULARITY_MIN:
            continue
        # A dial is filled, not a thin ring of foliage: check the enclosing
        # circle is mostly covered by the contour.
        (_, _), radius = cv2.minEnclosingCircle(contour)
        coverage = area / max(np.pi * radius * radius, 1.0)
        if coverage < 0.55:
            continue
        score = min(circularity, 1.0) * min(area / (frame_area * 0.08), 1.0)
        if score > best_score:
            best_score, best_area = score, area / frame_area

    if best_score > 0:
        return SubjectCheck(
            passed=True, score=float(min(best_score * 2.2, 1.0)),
            reasons=[f"round dial found, filling {best_area:.0%} of the frame"],
        )
    return SubjectCheck(
        passed=False, score=0.0,
        reasons=["no round compass dial in frame"],
    )


def has_meter_reading(path: Path) -> SubjectCheck:
    """True when a display panel showing digits is in frame.

    Any inclinometer in this survey has a light LCD carrying large dark digits.
    Requiring the digits — not merely the instrument — is what the rule asks for:
    a photo of the meter with a blank or unreadable display does not evidence a
    measurement.
    """
    gray, hsv = _load_gray_hsv(path)
    saturation, value = hsv[:, :, 1], hsv[:, :, 2]
    frame_area = float(gray.size)

    # Candidate panels: pale, low-saturation, solid rectangles.
    panel = ((saturation < 80) & (value > 105) & (value < 252)).astype(np.uint8)
    panel = cv2.morphologyEx(panel, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    # RETR_EXTERNAL so a panel is measured by its outer boundary, holes and all.
    contours, _ = cv2.findContours(panel, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best_digits = 0
    best_score = 0.0
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if not frame_area * PANEL_AREA_MIN <= area <= frame_area * PANEL_AREA_MAX:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if w < 12 or h < 8:
            continue
        fill = area / max(float(w * h), 1.0)
        aspect = max(w, h) / max(min(w, h), 1.0)
        if fill < PANEL_FILL_MIN or aspect > 7.0:
            continue

        inside = gray[y:y + h, x:x + w]
        if inside.size < 60:
            continue
        # Digits are markedly darker than the panel they sit on.
        threshold = max(int(inside.mean()) - 30, 15)
        dark = (inside < threshold).astype(np.uint8)
        n_glyphs, _, glyph_stats, _ = cv2.connectedComponentsWithStats(
            dark, connectivity=8
        )
        # Digits sit in a row: similar heights, roughly aligned tops.
        glyphs = []
        for g in range(1, n_glyphs):
            g_area = glyph_stats[g, cv2.CC_STAT_AREA]
            g_h = int(glyph_stats[g, cv2.CC_STAT_HEIGHT])
            g_top = int(glyph_stats[g, cv2.CC_STAT_TOP])
            if inside.size * 0.008 < g_area < inside.size * 0.4 and g_h > h * 0.15:
                glyphs.append((g_top, g_h))
        digits = 0
        if len(glyphs) >= READING_MIN_DIGITS:
            median_h = sorted(g[1] for g in glyphs)[len(glyphs) // 2]
            median_top = sorted(g[0] for g in glyphs)[len(glyphs) // 2]
            digits = sum(
                1 for top, gh in glyphs
                if abs(gh - median_h) <= median_h * 0.45
                and abs(top - median_top) <= median_h * 0.6
            )
        if digits >= READING_MIN_DIGITS:
            score = min(digits / 4.0, 1.0) * min(area / (frame_area * 0.05), 1.0)
            if score > best_score:
                best_score, best_digits = score, digits

    if best_digits >= READING_MIN_DIGITS:
        return SubjectCheck(
            passed=True, score=float(min(best_score * 2.0, 1.0)),
            reasons=[f"display panel showing {best_digits} digits"],
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
