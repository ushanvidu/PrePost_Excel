"""Turn a sector's loose photos into the slots the workbook expects.

The sheet has five band rows per sector, but the antenna has four RET ports, and
only two facts about band are recoverable from a photograph:

* **Low band.** 850 and 900 share one array and one adjuster, so there is one
  photograph, and it belongs in both rows. The red-painted ring marks that
  adjuster, so when the ring detector fires the low-band rows can be filled.
* **High band.** 1800-1, 1800-2 and 2100 sit on three electrically identical
  arrays. Which band feeds which port is decided by RRU cabling and leaves no
  trace in the image. These are offered to the engineer as ranked candidates and
  assigned by hand — once per site, then remembered.

Nothing here guesses a high band. A wrong photo under a band heading in an audit
document is worse than an empty slot, so unresolved slots stay empty and say so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..catalog import ELECTRICAL_TILT_CATEGORIES, MECHANICAL_AZIMUTH_CATEGORIES
from . import labels, ports
from .predict import SectorPrediction, predict_sector
from .store import ConfirmationStore

# The two rows that share one photograph.
LOW_BAND_ROWS = ("850_Tilt", "900_Tilt")
# The three rows that need a human decision.
HIGH_BAND_ROWS = ("1800_Tilt_1", "1800_Tilt_2", "2100_Tilt")

# A single-slot category is only filled unattended above this score. Below it
# the photo is offered as a candidate instead. Chosen from the measured set:
# every pick at or above this line was correct, and every mistake fell below it.
MIN_AUTOFILL_SCORE = 0.30

# Sheet row -> the visual class whose ranking fills it.
RIGHT_SIDE_ROWS = {
    "Antenna_M_Tilt": labels.MECHANICAL_TILT,
    "Antenna_Azimuth_Photo": labels.AZIMUTH,
    "Antenna_Coverage_Photo": labels.COVERAGE,
}


@dataclass
class SlotFill:
    """A proposal for one slot in the sheet."""

    row: str                       # survey category, e.g. "850_Tilt"
    photo: Path | None = None
    confidence: float = 0.0
    confirmed: bool = False
    needs_decision: bool = False   # true when only a human can resolve it
    reasons: list[str] = field(default_factory=list)
    alternatives: list[Path] = field(default_factory=list)

    @property
    def filled(self) -> bool:
        return self.photo is not None


@dataclass
class SectorAssignment:
    """Every slot for one sector, plus what was left over."""

    sector: str
    slots: dict[str, SlotFill] = field(default_factory=dict)
    unassigned: list[Path] = field(default_factory=list)

    @property
    def filled_count(self) -> int:
        return sum(1 for s in self.slots.values() if s.filled)

    @property
    def needs_decision(self) -> list[SlotFill]:
        return [s for s in self.slots.values() if s.needs_decision]


def assign_sector(
    sector_dir: Path,
    model,
    store: ConfirmationStore | None = None,
    profile: ports.AntennaProfile = ports.DEFAULT_PROFILE,
    site: str = "",
) -> SectorAssignment:
    """Propose a photo for every slot in one sector."""
    prediction: SectorPrediction = predict_sector(sector_dir, model, store)
    assignment = SectorAssignment(sector=sector_dir.name)
    used: set[Path] = set()

    # Photos the classifier reads as electrical tilt are reserved for the band
    # rows. Without this the single-slot loop below, which runs first, can take
    # a tilt photo for the mechanical-tilt slot and leave the band rows empty.
    reserved = {c.path for c in prediction.electrical_tilt_photos()}

    # --- the three single-slot categories on the right of the sheet ----------
    for row, category in RIGHT_SIDE_ROWS.items():
        ranked = [c for c in prediction.ranked.get(category, [])
                  if c.path not in used and c.path not in reserved]
        slot = SlotFill(row=row)
        best = ranked[0] if ranked else None
        if best is not None and (best.confirmed or best.score >= MIN_AUTOFILL_SCORE):
            slot.photo = best.path
            slot.confidence = best.score
            slot.confirmed = best.confirmed
            slot.reasons.append(
                "confirmed by you" if best.confirmed
                else f"best match for {labels.DISPLAY[category].lower()}"
                     f" ({best.score:.0%})"
            )
            slot.alternatives = [c.path for c in ranked[1:3]]
            used.add(best.path)
        elif best is not None:
            # Something scored, but not well enough to place unattended. On the
            # measured set every confident pick was right and the mistakes all
            # came in below this line, so a weak best guess is offered as a
            # candidate rather than written into the sheet.
            slot.needs_decision = True
            slot.confidence = best.score
            slot.reasons.append(
                f"best candidate only scored {best.score:.0%} — too low to place "
                f"without you"
            )
            slot.alternatives = [c.path for c in ranked[:3]]
        else:
            slot.needs_decision = True
            slot.reasons.append("no candidate found in this sector")
        assignment.slots[row] = slot

    # --- electrical tilt: split low band from high band ----------------------
    tilt_photos = [c.path for c in prediction.electrical_tilt_photos()
                   if c.path not in used]
    evidence = {p: ports.analyse(p, profile) for p in tilt_photos}

    confirmed_rows = _confirmed_band_rows(tilt_photos, store)

    low = [p for p in tilt_photos if evidence[p].band_group == "low"]
    low.sort(key=lambda p: -evidence[p].confidence)
    high = [p for p in tilt_photos if evidence[p].band_group != "low"]

    # 850 and 900 are one photograph in two rows.
    low_photo = confirmed_rows.get("850_Tilt") or (low[0] if low else None)
    for row in LOW_BAND_ROWS:
        slot = SlotFill(row=row)
        chosen = confirmed_rows.get(row) or low_photo
        if chosen is not None:
            slot.photo = chosen
            slot.confirmed = chosen in confirmed_rows.values()
            slot.confidence = 1.0 if slot.confirmed else evidence.get(
                chosen, ports.PortEvidence(path=chosen)
            ).confidence
            slot.reasons = (["confirmed by you"] if slot.confirmed
                            else list(evidence[chosen].reasons))
            slot.reasons.append(
                "850 and 900 share one array and one adjuster, so both rows "
                "take the same photograph"
            )
            slot.alternatives = [p for p in low[1:3]]
            used.add(chosen)
        else:
            slot.needs_decision = True
            slot.reasons.append("no red ring found on any tilt photo")
            slot.alternatives = list(high[:3])
        assignment.slots[row] = slot

    # High bands cannot be told apart from the image. Offer, never guess.
    remaining = [p for p in high if p not in used]
    for index, row in enumerate(HIGH_BAND_ROWS):
        slot = SlotFill(row=row)
        chosen = confirmed_rows.get(row)
        if chosen is not None:
            slot.photo = chosen
            slot.confirmed = True
            slot.confidence = 1.0
            slot.reasons.append("confirmed by you")
            used.add(chosen)
        else:
            slot.needs_decision = True
            slot.confidence = 0.0
            slot.reasons.append(
                "1800-1, 1800-2 and 2100 sit on identical arrays — the band "
                "comes from the RRU cabling, not the photo"
            )
            slot.alternatives = list(remaining)
        assignment.slots[row] = slot

    assignment.unassigned = [
        p for p in prediction.photos
        if p not in used and prediction.best_guess.get(p) != labels.OTHER
    ]
    return assignment


def _confirmed_band_rows(
    photos: list[Path], store: ConfirmationStore | None
) -> dict[str, Path]:
    """Band rows the engineer has already decided, from the confirmation store.

    Band choices ride in the store's `port` field: it records the sheet row the
    engineer put the photo in, which for tilt photos is the decision that matters.
    """
    if store is None:
        return {}
    resolved: dict[str, Path] = {}
    for photo in photos:
        record = store.get(photo)
        if record is not None and record.port:
            resolved[record.port] = photo
    return resolved


def assign_site(
    site_dir: Path, model, store: ConfirmationStore | None = None,
    profile: ports.AntennaProfile = ports.DEFAULT_PROFILE,
) -> dict[str, SectorAssignment]:
    """Propose slot fills for every sector folder under a site."""
    sectors = sorted(
        d for d in site_dir.iterdir() if d.is_dir() and not d.name.startswith(".")
    )
    return {
        d.name: assign_sector(d, model, store, profile, site=site_dir.name)
        for d in sectors
    }


ALL_ROWS = list(ELECTRICAL_TILT_CATEGORIES) + list(MECHANICAL_AZIMUTH_CATEGORIES)


def to_post_photos(
    assignments: dict[str, SectorAssignment]
) -> dict[tuple[int, str], list[Path]]:
    """Flatten sector assignments into the mapping ``plan.build_plan`` wants.

    Sector folders are named ``S1``, ``S2``…; the workbook keys sectors by number.
    Slots that still need a human decision are simply absent, so they keep their
    empty drop box.
    """
    resolved: dict[tuple[int, str], list[Path]] = {}
    for name, assignment in assignments.items():
        digits = "".join(ch for ch in name if ch.isdigit())
        if not digits:
            continue
        sector = int(digits)
        for row, slot in assignment.slots.items():
            if slot.filled:
                resolved[(sector, row)] = [slot.photo]
    return resolved
