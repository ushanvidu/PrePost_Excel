"""Turn a photo into a fixed-length feature vector for category classification.

Deliberately classical: colour, layout, and edge statistics rather than a neural
embedding. The four categories that matter are separated by properties a
histogram can see —

    mechanical tilt  a large saturated blue body (the angle gauge)
    azimuth          a saturated green/yellow disc (the compass)
    coverage         sky along the top, dense foliage below, no near subject
    electrical tilt  a pale antenna underside with dark connectors and a hand

— so a few hundred numbers do the job with no model download, no GPU, and no
data leaving the machine. That matters on an 8 GB laptop, and it matters more
because these photos carry site IDs and GPS burned into the frame.

Every image is resized to a common working size first, so a 9.6 MP original and
a 1.2 MP WhatsApp copy of the same scene produce comparable vectors.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

# Working size for analysis. Small enough to be fast, large enough that the
# tilt scale and connectors still register as edges.
WORK_W, WORK_H = 384, 384

# Coarse HSV histogram shape. 12 hues x 3 saturation x 3 value = 108 bins.
H_BINS, S_BINS, V_BINS = 12, 3, 3

# Hue ranges in OpenCV's 0-179 scale, chosen from the actual subjects.
HUE_BANDS = {
    "red_low": (0, 8),        # the painted ring on the r1 connector
    "orange_skin": (8, 22),   # hands, terracotta roofs
    "yellow": (22, 34),       # compass body, cable tags
    "green": (34, 85),        # foliage, compass housing
    "cyan_sky": (85, 100),    # hazy sky
    "blue": (100, 130),       # the angle gauge body, deep sky
    "magenta": (130, 179),
}

FEATURE_NAMES: list[str] = []


def _load_bgr(path: Path) -> np.ndarray:
    """Load an image, honour EXIF rotation, and resize to the working size."""
    with Image.open(path) as img:
        img.load()
        oriented = ImageOps.exif_transpose(img).convert("RGB")
        resized = oriented.resize((WORK_W, WORK_H), Image.LANCZOS)
        rgb = np.asarray(resized)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _aspect(path: Path) -> float:
    """Portrait/landscape ratio, taken before the square resize destroys it."""
    with Image.open(path) as img:
        oriented = ImageOps.exif_transpose(img)
        w, h = oriented.size
    return w / h if h else 1.0


def describe(path: Path) -> np.ndarray:
    """Return the feature vector for one image."""
    bgr = _load_bgr(path)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    parts: list[np.ndarray] = []
    names: list[str] = []

    # --- global HSV histogram, normalised so it describes proportion not size
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [H_BINS, S_BINS, V_BINS],
                        [0, 180, 0, 256, 0, 256])
    hist = (hist / max(hist.sum(), 1)).flatten()
    parts.append(hist)
    names += [f"hsvhist_{i}" for i in range(hist.size)]

    # --- how much of the frame each meaningful hue occupies, at two saturations.
    # Saturated masks find painted objects (gauge, compass, ring); the looser
    # mask finds washed-out sky and pale antenna plastic.
    for label, (lo, hi) in HUE_BANDS.items():
        in_hue = (h >= lo) & (h < hi)
        parts.append(np.array([
            float((in_hue & (s > 90)).mean()),   # vivid
            float((in_hue & (s > 40)).mean()),   # any colour at all
        ]))
        names += [f"hue_{label}_vivid", f"hue_{label}_any"]

    # --- achromatic content: the antenna underside and the scale strip are
    # pale and unsaturated; a blank radome is almost entirely so.
    parts.append(np.array([
        float(((s < 40) & (v > 170)).mean()),   # pale / white
        float(((s < 40) & (v < 70)).mean()),    # dark grey / black connectors
        float(s.mean()) / 255.0,
        float(s.std()) / 255.0,
        float(v.mean()) / 255.0,
        float(v.std()) / 255.0,
    ]))
    names += ["pale_frac", "dark_frac", "sat_mean", "sat_std", "val_mean", "val_std"]

    # --- spatial layout on a 3x3 grid. Coverage shots put sky on the top row
    # and foliage below; close-ups of hardware do not.
    cell_h, cell_w = WORK_H // 3, WORK_W // 3
    for row in range(3):
        for col in range(3):
            ys = slice(row * cell_h, (row + 1) * cell_h)
            xs = slice(col * cell_w, (col + 1) * cell_w)
            cell_h_, cell_s_, cell_v_ = h[ys, xs], s[ys, xs], v[ys, xs]
            sky = ((cell_h_ >= 85) & (cell_h_ < 130) & (cell_v_ > 120)).mean()
            veg = ((cell_h_ >= 34) & (cell_h_ < 85) & (cell_s_ > 50)).mean()
            parts.append(np.array([
                float(cell_s_.mean()) / 255.0,
                float(cell_v_.mean()) / 255.0,
                float(sky),
                float(veg),
            ]))
            names += [f"cell{row}{col}_{k}" for k in ("sat", "val", "sky", "veg")]

    # --- texture. Foliage is a dense edge field; a radome or a gauge face is flat.
    edges = cv2.Canny(gray, 60, 160)
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    thirds = [edges[: WORK_H // 3], edges[WORK_H // 3: 2 * WORK_H // 3],
              edges[2 * WORK_H // 3:]]
    parts.append(np.array([
        float(edges.mean()) / 255.0,
        *[float(t.mean()) / 255.0 for t in thirds],
        float(lap.var()) / 10000.0,            # focus / detail energy
        float(gray.std()) / 255.0,
    ]))
    names += ["edge_density", "edge_top", "edge_mid", "edge_bot",
              "laplacian_var", "gray_std"]

    # --- straight lines. The gauge and the antenna plate have long straight
    # edges; foliage has none.
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=60,
                            minLineLength=WORK_W // 4, maxLineGap=10)
    parts.append(np.array([0.0 if lines is None else min(len(lines), 60) / 60.0]))
    names.append("long_line_frac")

    # --- circles. The compass dial and the painted ring are round; nothing
    # else in the set is.
    blurred = cv2.medianBlur(gray, 5)
    circles = cv2.HoughCircles(blurred, cv2.HOUGH_GRADIENT, dp=1.2,
                               minDist=WORK_W // 4, param1=120, param2=60,
                               minRadius=WORK_W // 12, maxRadius=WORK_W // 2)
    parts.append(np.array([0.0 if circles is None else min(len(circles[0]), 6) / 6.0]))
    names.append("circle_frac")

    # --- object-level features.
    # Pixel fractions cannot separate an electrical-tilt photo from a ports
    # photo: both are the same antenna underside. What separates them is the
    # tilt scale (a thin bright strip pulled out of the plate) and the hand
    # holding it. Likewise a blue *object* is a gauge, while blue *pixels* may
    # only be sky. So these look for coherent regions, not colour counts.
    parts.append(_object_features(hsv, gray, h, s, v))
    names += OBJECT_FEATURE_NAMES

    # --- instrument signals.
    # Azimuth must show a compass and mechanical tilt a meter with a reading.
    # Tried first as hard gates with hand-picked thresholds; measured against
    # 157 photos the engineers had placed, the strict version recognised 15% of
    # real compasses and 0% of real meters, and the loose version accepted
    # everything. So the evidence goes in as features and the classifier decides
    # how much it is worth, using real Post photos as training data.
    parts.append(_instrument_features(gray))
    names += INSTRUMENT_FEATURE_NAMES

    parts.append(np.array([_aspect(path)]))
    names.append("aspect")

    global FEATURE_NAMES
    if not FEATURE_NAMES:
        FEATURE_NAMES = names

    vector = np.concatenate(parts).astype(np.float32)
    return np.nan_to_num(vector, nan=0.0, posinf=0.0, neginf=0.0)


OBJECT_FEATURE_NAMES = [
    "device_blob_area", "device_blob_fill", "device_blob_extent",
    "compass_blob_area", "compass_blob_extent",
    "skin_area", "skin_largest",
    "strip_score", "strip_count", "strip_best_elong",
    "pale_blob_area", "dark_blob_count",
    "lcd_score", "lcd_fill", "lcd_digit_count",
    "vial_score", "roundness_best",
]


def _find_lcd(gray: np.ndarray, s: np.ndarray, v: np.ndarray) -> tuple[float, float, float]:
    """Look for a digital display: a pale rectangle holding dark digits.

    Deliberately colour-blind. The Pre survey used a green Digi-Pas and the Post
    survey a blue gauge, so any colour-keyed detector learned on one fails on the
    other. Both instruments have an LCD, so the display is what generalises.
    """
    panel = ((s < 70) & (v > 110) & (v < 250)).astype(np.uint8)
    panel = cv2.morphologyEx(panel, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    count, lab, stats, _ = cv2.connectedComponentsWithStats(panel, connectivity=8)

    best_score = best_fill = 0.0
    best_digits = 0
    for i in range(1, count):
        area = float(stats[i, cv2.CC_STAT_AREA])
        if area < gray.size * 0.01:
            continue
        x, y = int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP])
        w, ht = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
        fill = area / max(w * ht, 1.0)
        aspect = max(w, ht) / max(min(w, ht), 1.0)
        if fill < 0.55 or aspect > 6.0:
            continue                      # not a solid, roughly rectangular panel
        # Count dark glyph blobs inside it — LCD digits.
        inside = gray[y:y + ht, x:x + w]
        if inside.size == 0:
            continue
        dark = (inside < max(int(inside.mean()) - 35, 20)).astype(np.uint8)
        n, _, dstats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
        digits = sum(1 for j in range(1, n)
                     if inside.size * 0.01 < dstats[j, cv2.CC_STAT_AREA] < inside.size * 0.35)
        score = min(area / gray.size, 0.5) * 2 * fill * min(digits / 4.0, 1.0)
        if score > best_score:
            best_score, best_fill, best_digits = score, fill, digits
    return best_score, best_fill, min(best_digits, 8) / 8.0


def _find_vials(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> float:
    """Spirit-level vials: small, very saturated green-yellow capsules.

    Present on both inclinometers, so this survives the tool change too.
    """
    mask = ((h >= 25) & (h < 75) & (s > 130) & (v > 140)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    score = 0.0
    for i in range(1, count):
        area = float(stats[i, cv2.CC_STAT_AREA])
        w = float(stats[i, cv2.CC_STAT_WIDTH])
        ht = float(stats[i, cv2.CC_STAT_HEIGHT])
        if area < mask.size * 0.0006 or area > mask.size * 0.06:
            continue
        elong = max(w, ht) / max(min(w, ht), 1.0)
        if 1.6 <= elong <= 9.0 and area / max(w * ht, 1.0) > 0.5:
            score = max(score, min(area / (mask.size * 0.03), 1.0))
    return score


def _best_roundness(gray: np.ndarray) -> float:
    """Roundness of the most circular sizeable contour — the compass dial."""
    edges = cv2.Canny(cv2.medianBlur(gray, 5), 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = 0.0
    for c in contours:
        area = cv2.contourArea(c)
        if area < gray.size * 0.02:
            continue
        perimeter = cv2.arcLength(c, True)
        if perimeter <= 0:
            continue
        circularity = 4 * np.pi * area / (perimeter ** 2)   # 1.0 for a circle
        best = max(best, min(float(circularity), 1.0))
    return best


def _largest_component(mask: np.ndarray) -> tuple[float, float, float]:
    """Area fraction, fill ratio and bbox extent of the biggest blob in a mask.

    Returns zeros when the mask is empty. `fill` is blob area over its bounding
    box — a solid device fills its box, scattered sky pixels do not.
    """
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN,
                            np.ones((5, 5), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        return 0.0, 0.0, 0.0
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    area = float(stats[biggest, cv2.CC_STAT_AREA])
    w = float(stats[biggest, cv2.CC_STAT_WIDTH])
    ht = float(stats[biggest, cv2.CC_STAT_HEIGHT])
    total = mask.size
    box = max(w * ht, 1.0)
    return area / total, area / box, (w * ht) / total


def _object_features(hsv, gray, h, s, v) -> np.ndarray:
    """Coherent-region features for the four subjects that matter."""
    # A handheld instrument: any large, solid, saturated body. Colour-agnostic,
    # because the survey tool changed between Pre (green) and Post (blue).
    device_area, device_fill, device_extent = _largest_component(
        (s > 90) & (v > 40) & ~((h >= 34) & (h < 85) & (v < 150))   # exclude foliage
    )

    # The compass: saturated green/yellow housing, usually round.
    compass_area, _, compass_extent = _largest_component(
        (h >= 22) & (h < 85) & (s > 110) & (v > 90)
    )

    # Hands and gloves. Gloves in this set are grey-brown, bare hands orange.
    skin_mask = (h >= 3) & (h < 25) & (s > 45) & (s < 200) & (v > 60)
    skin_area = float(skin_mask.mean())
    skin_largest, _, _ = _largest_component(skin_mask)

    # The tilt scale: a thin, bright, low-saturation strip standing clear of
    # the plate. Found as elongated bright components rather than by colour.
    bright = ((s < 60) & (v > 175)).astype(np.uint8)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(bright, connectivity=8)
    strip_count = 0
    best_elong = 0.0
    strip_score = 0.0
    for i in range(1, count):
        w = float(stats[i, cv2.CC_STAT_WIDTH])
        ht = float(stats[i, cv2.CC_STAT_HEIGHT])
        area = float(stats[i, cv2.CC_STAT_AREA])
        if area < 60 or min(w, ht) < 2:
            continue
        elong = max(w, ht) / max(min(w, ht), 1.0)
        fill = area / max(w * ht, 1.0)
        if elong >= 3.0 and fill > 0.45:     # long, thin, and solid
            strip_count += 1
            best_elong = max(best_elong, elong)
            strip_score = max(strip_score, min(elong / 12.0, 1.0) * fill)
    pale_area, _, _ = _largest_component((s < 45) & (v > 160))

    # Connector count: the underside carries a row of dark round collars.
    dark = ((v < 80)).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n_dark, _, dstats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    dark_blobs = sum(1 for i in range(1, n_dark)
                     if dstats[i, cv2.CC_STAT_AREA] > gray.size * 0.004)

    lcd_score, lcd_fill, lcd_digits = _find_lcd(gray, s, v)
    vial = _find_vials(h, s, v)
    roundness = _best_roundness(gray)

    return np.array([
        device_area, device_fill, device_extent,
        compass_area, compass_extent,
        skin_area, skin_largest,
        strip_score, min(strip_count, 20) / 20.0, min(best_elong, 20.0) / 20.0,
        pale_area, min(dark_blobs, 12) / 12.0,
        lcd_score, lcd_fill, lcd_digits,
        vial, roundness,
    ], dtype=np.float32)


INSTRUMENT_FEATURE_NAMES = [
    "dial_radius", "dial_count", "dial_centred",
    "digits_dark_on_light", "digits_light_on_dark",
]


def _instrument_features(gray: np.ndarray) -> np.ndarray:
    """Evidence of a compass dial and of a display showing a value."""
    from .subjects import digit_row_length

    height, width = gray.shape[:2]
    short_edge = min(width, height)

    # A compass rim is broken by tick marks and numbers, so vote on arcs rather
    # than looking for a clean contour.
    circles = cv2.HoughCircles(
        cv2.medianBlur(gray, 5), cv2.HOUGH_GRADIENT, dp=1.2,
        minDist=short_edge * 0.35, param1=110, param2=60,
        minRadius=int(short_edge * 0.13), maxRadius=int(short_edge * 0.62),
    )
    if circles is None:
        dial_radius = dial_count = dial_centred = 0.0
    else:
        found = circles[0]
        best = max(found, key=lambda c: c[2])
        dial_radius = float(best[2]) / short_edge
        dial_count = min(len(found), 4) / 4.0
        # A held-up compass sits near the middle of the frame.
        dx = (float(best[0]) - width / 2) / max(width, 1)
        dy = (float(best[1]) - height / 2) / max(height, 1)
        dial_centred = float(max(0.0, 1.0 - 2.0 * np.hypot(dx, dy)))

    equalised = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
    frame_area = float(gray.size)
    kernel = np.ones((2, 2), np.uint8)
    scores = []
    for invert in (cv2.THRESH_BINARY_INV, cv2.THRESH_BINARY):
        mask = cv2.adaptiveThreshold(
            equalised, 255, cv2.ADAPTIVE_THRESH_MEAN_C, invert, 31, 12
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        scores.append(min(digit_row_length(mask, frame_area), 8) / 8.0)

    return np.array([dial_radius, dial_count, dial_centred, *scores],
                    dtype=np.float32)


def describe_many(paths: list[Path]) -> np.ndarray:
    """Feature matrix for a list of images, one row each."""
    return np.vstack([describe(p) for p in paths]) if paths else np.empty((0, 0))
