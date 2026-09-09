"""Filename parsing: the category/sequence split is the part that bites."""

from pathlib import Path

import pytest

from antenna_audit import catalog

PREFIX = "Sector_1_RF_Antenna_Photos_Sector_1_RF_Antenna_Photos_"


@pytest.mark.parametrize(
    "filename, sector, category, sequence",
    [
        # The trailing _1 is a sequence number.
        (f"{PREFIX}Ant_Sec_1__850_Tilt_1.jpg", 1, "850_Tilt", 1),
        (f"{PREFIX}Ant_Sec_1__2100_Tilt_1.jpg", 1, "2100_Tilt", 1),
        # Here the category itself ends in a digit and the sequence follows it.
        (f"{PREFIX}Ant_Sec_1__1800_Tilt_1_1.jpg", 1, "1800_Tilt_1", 1),
        (f"{PREFIX}Ant_Sec_1__1800_Tilt_2_1.jpg", 1, "1800_Tilt_2", 1),
        # ...and again, with a real second shot of the same subject.
        (f"{PREFIX}Ant_Sec_3__1800_Tilt_2_4.jpg", 3, "1800_Tilt_2", 4),
        (f"{PREFIX}Ant_Sec_2_RRU_2_Lable_Photo_1.jpg", 2, "RRU_2_Lable_Photo", 1),
        (f"{PREFIX}Ant_Sec_4_Antenna_M_Tilt_2.jpeg", 4, "Antenna_M_Tilt", 2),
        (f"{PREFIX}Ant_Sec_2_Antenna_Coverage_Photo_1.jpg", 2,
         "Antenna_Coverage_Photo", 1),
        # A different naming family entirely.
        ("Existing_Antenna_Location_over_Sec_3_Mobitel_Antenna_Location_2.png",
         3, "Mobitel_Antenna_Location", 2),
    ],
)
def test_classify(filename, sector, category, sequence):
    photo = catalog.classify(Path("/photos") / filename)
    assert photo is not None
    assert (photo.sector, photo.category, photo.sequence) == (
        sector, category, sequence,
    )


def test_non_images_are_ignored():
    assert catalog.classify(Path("/photos/.DS_Store")) is None
    assert catalog.classify(Path("/photos/notes.txt")) is None


def test_unknown_category_still_parses():
    """A category we have never seen must not crash or swallow the sector."""
    photo = catalog.classify(Path(f"/p/{PREFIX}Ant_Sec_2_Brand_New_Photo_3.jpg"))
    assert photo is not None
    assert photo.sector == 2
    assert photo.category == "Brand_New_Photo"
    assert photo.sequence == 3


def test_headings_match_the_reference_workbook():
    assert catalog.heading_for("850_Tilt", 1) == "Sec 1_ 850 Tilt"
    assert catalog.heading_for("1800_Tilt_2", 3) == "Sec 3_ 1800 Tilt 2"
    assert catalog.heading_for("Antenna_M_Tilt", 2) == "Sec 2 Antenna M Tilt"
    assert (
        catalog.heading_for("Antenna_Azimuth_Photo", 4)
        == "Sec 4 Antenna Azimuth Photo"
    )
