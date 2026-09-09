"""The category vocabulary the classifier works in, and how it maps to filenames.

The Pre photos are already labelled by their filenames, so they are the training
set. The survey categories are finer-grained than the classifier needs — every
band of electrical tilt is the same *kind* of photograph — so they collapse to
five visual classes.

Band is deliberately absent. The antenna's five band rows sit on four RET ports,
850 and 900 share one array, and the three high-band arrays are electrically
identical, so band is not a property of the image. It is resolved from port
identity plus a per-site mapping, not by this classifier.
"""

from __future__ import annotations

ELECTRICAL_TILT = "electrical_tilt"
MECHANICAL_TILT = "mechanical_tilt"
AZIMUTH = "azimuth"
COVERAGE = "coverage"
OTHER = "other"

CATEGORIES = [ELECTRICAL_TILT, MECHANICAL_TILT, AZIMUTH, COVERAGE, OTHER]

# Survey category (from catalog.py) -> visual class.
FROM_SURVEY = {
    "850_Tilt": ELECTRICAL_TILT,
    "900_Tilt": ELECTRICAL_TILT,
    "1800_Tilt_1": ELECTRICAL_TILT,
    "1800_Tilt_2": ELECTRICAL_TILT,
    "2100_Tilt": ELECTRICAL_TILT,
    "Antenna_M_Tilt": MECHANICAL_TILT,
    "Antenna_Azimuth_Photo": AZIMUTH,
    "Antenna_Coverage_Photo": COVERAGE,
}

# Human-facing names, used in the review screen.
DISPLAY = {
    ELECTRICAL_TILT: "Electrical tilt",
    MECHANICAL_TILT: "Mechanical tilt",
    AZIMUTH: "Azimuth",
    COVERAGE: "Coverage",
    OTHER: "Other",
}


def visual_class(survey_category: str) -> str:
    """Map a survey category to the class the classifier predicts."""
    return FROM_SURVEY.get(survey_category, OTHER)
