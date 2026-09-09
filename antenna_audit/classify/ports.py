"""Work out which RET port an electrical-tilt photo shows.

Band is not visible in these photos — 850 and 900 share one array, and the three
high-band arrays are electrically identical (see `bands.py`). What *is* visible
is which adjuster the engineer was measuring, and that is what this module
recovers.

**Only one cue survived measurement: the painted red ring.** One connector on
each antenna carries a red-painted ring marking the low-band RET, so a ring in
frame means the photo is the low band (850/900) and its absence means one of the
three high-band ports.

Two other cues were tried and rejected on evidence, recorded here so nobody
spends the afternoon again:

* **OCR of the moulded port codes** (`r1`, `RBy3`, `RTy2`, `Ly1`). A human can
  read these in most frames, so it looked promising. Tesseract 5.5.3 recovered a
  correct code in **0 of 12** frames — including the one frame showing all four
  codes at once, where its raw output was noise. The lettering is moulded rather
  than printed, low-contrast, upside down (these antennas are inverted-mount),
  curved, corroded, and compressed by WhatsApp to 1.2 MP. The OCR path was
  removed rather than shipped as a dependency that contributes nothing.
* **Shooting order.** Measured across four sectors: ad-hoc, no usable pattern.

The ring detector is a *hint*, not a verdict — it scored 9 of 13 on the only
labelled photos available, with its threshold tuned on those same photos. High
band ports are therefore assigned by the engineer in the review screen, which is
what the plan called for anyway. Every function returns evidence with a score so
the screen can show why a suggestion was made.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

# Analysis size. Larger than the category classifier's working size because the
# port lettering is small and needs the pixels.
WORK_LONG_EDGE = 900


@dataclass(frozen=True)
class AntennaProfile:
    """The port layout of one antenna model."""

    model: str
    ports: tuple[str, ...]        # in physical order along the underside
    low_band_port: str            # the red-ringed one: carries 850 and 900
    aliases: dict[str, str] = field(default_factory=dict)

    def normalise(self, text: str) -> str | None:
        """Map a scrap of OCR text onto a known port code, or None."""
        squashed = re.sub(r"[^A-Za-z0-9]", "", text).upper()
        if not squashed:
            return None
        for port in self.ports:
            if re.sub(r"[^A-Za-z0-9]", "", port).upper() == squashed:
                return port
        return self.aliases.get(squashed)


# The model on GMNIT1 sectors 1-3 and most other sites in this survey. Its label
# sticker lists exactly four RET serials, ending r1 / RBy3 / RTy2 / Ly1.
AQU4518 = AntennaProfile(
    model="AQU4518R9v06",
    ports=("r1", "RBy3", "RTy2", "Ly1"),
    low_band_port="r1",
    aliases={"R1": "r1", "RI": "r1", "RBY3": "RBy3", "RTY2": "RTy2",
             "LY1": "Ly1", "LYI": "Ly1", "RBY": "RBy3", "RTY": "RTy2"},
)

# GMNIT1 sector 4 is a different unit: five scales, printed rather than moulded
# labels, and a red ring on the connector marked CR1.
FIVE_PORT = AntennaProfile(
    model="unknown-5-port",
    ports=("Y1", "R1", "Y3-Y6", "R2", "Y2"),
    low_band_port="R1",
    aliases={"CR1": "R1", "Y3Y6": "Y3-Y6"},
)

PROFILES = {p.model: p for p in (AQU4518, FIVE_PORT)}
DEFAULT_PROFILE = AQU4518


@dataclass
class RingDetection:
    """A red-painted ring found in the frame."""

    cx: float        # centre, normalised 0-1 across the frame
    cy: float
    radius: float    # normalised to the frame's short edge
    score: float     # 0-1, how ring-like and how red
    at_edge: bool    # touching the frame border, so possibly clipped


@dataclass
class PortEvidence:
    """Everything the reader could establish about one tilt photo."""

    path: Path
    rings: list[RingDetection] = field(default_factory=list)
    port: str | None = None             # only ever the low-band port, or None
    band_group: str = "unknown"         # "low" | "high" | "unknown"
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def has_ring(self) -> bool:
        return bool(self.rings)


def _load(path: Path) -> np.ndarray:
    """Load at analysis resolution, EXIF-corrected, as BGR."""
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
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


# Saturation floor for "this is paint, not rust".
#
# These antennas are corroded, and rust reads as red to any ordinary threshold —
# in one photo a permissive mask matched 17% of the frame. Sweeping the floor
# over the labelled photos showed the separation appears only above ~190: at that
# point ringed photos average 0.064% red pixels against 0.010% for the rest, a
# six-fold gap, where a looser mask gives no separation at all.
#
# The threshold was chosen on 13 labelled photos from one site, so treat it as
# provisional until more sites are labelled. The mechanism generalises even if
# the exact number moves.
PAINT_SATURATION_MIN = 190
PAINT_VALUE_MIN = 60

# Fraction of the frame that must be painted red before a ring is considered
# present at all, and the fraction at which the evidence is treated as strong.
PAINT_FRACTION_MIN = 0.00015
PAINT_FRACTION_STRONG = 0.0010


def _red_mask(hsv: np.ndarray) -> np.ndarray:
    """Pixels that are painted red. Red wraps the hue circle, so two ranges."""
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    painted = (s > PAINT_SATURATION_MIN) & (v > PAINT_VALUE_MIN)
    return (((h <= 8) | (h >= 172)) & painted).astype(np.uint8)


def find_red_rings(path: Path) -> list[RingDetection]:
    """Find red-painted connector rings.

    Scores on redness, roundness and the ring's hollowness — the paint marks an
    annulus around a connector, so the centre is *not* red. That distinguishes it
    from a red roof or a solid red button, which are the common false positives.
    """
    bgr = _load(path)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    height, width = hsv.shape[:2]
    short_edge = min(width, height)

    mask = _red_mask(hsv)
    # The paint mask is sparse by design, so join neighbouring specks into the
    # arc they came from before measuring shape.
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))

    frame_area = float(width * height)
    painted_fraction = float(mask.mean())
    if painted_fraction < PAINT_FRACTION_MIN:
        return []                       # no meaningful paint anywhere in frame

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    found: list[RingDetection] = []

    for contour in contours:
        area = cv2.contourArea(contour)
        if area < frame_area * 3e-5:            # a few dozen pixels at this size
            continue
        (cx, cy), radius = cv2.minEnclosingCircle(contour)
        if radius < short_edge * 0.008:
            continue

        perimeter = cv2.arcLength(contour, True)
        circularity = 4 * np.pi * area / max(perimeter ** 2, 1.0)

        # Hollowness: paint marks an annulus around a connector, so the middle
        # should not be painted. This is what separates a ring from a red speck.
        inner = np.zeros(mask.shape, np.uint8)
        cv2.circle(inner, (int(cx), int(cy)), max(int(radius * 0.5), 1), 255, -1)
        inner_red = float(mask[inner > 0].mean()) if (inner > 0).any() else 1.0
        hollow = 1.0 - min(inner_red, 1.0)

        # How much of the frame's paint this blob accounts for: the real ring
        # dominates, stray specks do not.
        share = area / max(painted_fraction * frame_area, 1.0)

        score = float(np.clip(
            0.45 * min(painted_fraction / PAINT_FRACTION_STRONG, 1.0)
            + 0.25 * hollow
            + 0.15 * min(circularity / 0.5, 1.0)
            + 0.15 * min(share, 1.0),
            0.0, 1.0,
        ))
        margin = short_edge * 0.02
        at_edge = (cx - radius < margin or cy - radius < margin
                   or cx + radius > width - margin or cy + radius > height - margin)

        found.append(RingDetection(
            cx=cx / width, cy=cy / height, radius=radius / short_edge,
            score=score, at_edge=at_edge,
        ))

    found.sort(key=lambda r: -r.score)
    return found[:4]


def analyse(path: Path,
            profile: AntennaProfile = DEFAULT_PROFILE) -> PortEvidence:
    """Gather what can be established about one electrical-tilt photo."""
    evidence = PortEvidence(path=path)
    evidence.rings = find_red_rings(path)

    strong_rings = [r for r in evidence.rings if r.score >= 0.45]
    if strong_rings:
        best = strong_rings[0]
        evidence.band_group = "low"
        evidence.port = profile.low_band_port
        evidence.confidence = min(0.55 + 0.35 * best.score, 0.95)
        evidence.reasons.append(
            f"red ring found ({best.score:.0%} ring-like)"
            + (" at the frame edge" if best.at_edge else "")
        )
    else:
        evidence.band_group = "high"
        evidence.confidence = 0.45
        evidence.reasons.append("no red ring in frame")

    return evidence
