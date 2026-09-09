# Antenna Audit Photo Sheets

Builds one Excel workbook per site from a folder of field-survey photos, placing
every **Pre** photo under its heading and leaving aligned, labelled empty boxes
for the **Post** photos you paste in by hand afterwards.

Reverse-engineered from `Documents/GMTHI1 Antenna Audit Photos.xlsx`.

---

## The app

Double-click **`Antenna Audit App.command`**. It sets itself up the first time,
then opens in your browser.

Or from a terminal:

```bash
python -m antenna_audit web
```

Drop in as many photo folders as you like — **each folder becomes its own
workbook**. When the build finishes you can download them one at a time or all
together as a `.zip`.

### Two ways to give it folders

| | When to use it |
|---|---|
| **Upload folders** | Drag folders onto the drop zone, or click to pick one. Works from any machine, but every photo is copied into the browser and up to the app, so a large site folder takes a moment. |
| **Folder on this computer** | Type the path to a folder. The photos are read straight from disk with nothing copied, so it is close to instant. Use this when the photos are already on the machine running the app. |

Either way, **the folder directly containing the photos is the site name**. Point
it at a parent holding ten site folders and you get ten workbooks; point it at a
single site folder and you get one.

### What it tells you

Each folder gets a card showing its sector count, how many photos were placed,
and the file size. Expand a card to see which headings had no Pre photo, and
which photos were found but deliberately left off the sheet. Anything that fails
structural validation is marked and must not be used.

---

## Command line

Everything the app does is also available from a terminal.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# See what the app found before building anything
python -m antenna_audit report --images-root Documents/IMAGE-P202506262207_D001-20260

# Build every site
python -m antenna_audit build \
    --images-root Documents/IMAGE-P202506262207_D001-20260 \
    --out output

# One site, with a PNG preview you can check without opening Excel
python -m antenna_audit build \
    --images-root Documents/IMAGE-P202506262207_D001-20260 \
    --out output --site GMTHI1 --preview
```

Output is `output/<SITE> Antenna Audit Photos.xlsx` — **one workbook per site**.

---

## Sheet layout

Each sector is a band down the sheet, introduced by a dark merged `Sector N`
banner. Inside a sector the sheet splits in half:

```
┌──────────────────────────────────────┬──────────────────────────────────────┐
│            Electrical Tilt           │      Mechanical Tilt & Azimuth       │
├──────────────────┬───────────────────┼──────────────────┬───────────────────┤
│       Pre        │       Post        │       Pre        │       Post        │
├──────────────────┼───────────────────┼──────────────────┼───────────────────┤
│ Sec 1_ 850 Tilt  │ Sec 1_ 850 Tilt   │ Sec 1 Ant M Tilt │ Sec 1 Ant M Tilt  │
│ ┌──────────────┐ │ ┌ ─ ─ ─ ─ ─ ─ ─┐  │ ┌──────────────┐ │ ┌ ─ ─ ─ ─ ─ ─ ─┐  │
│ │   [photo]    │ │ │ paste here   │  │ │   [photo]    │ │ │ paste here   │  │
│ └──────────────┘ │ └ ─ ─ ─ ─ ─ ─ ─┘  │ └──────────────┘ │ └ ─ ─ ─ ─ ─ ─ ─┘  │
│ Sec 1_ 900 Tilt  │ Sec 1_ 900 Tilt   │ Sec 1 Azimuth    │ Sec 1 Azimuth     │
│        …         │         …         │        …         │         …         │
└──────────────────┴───────────────────┴──────────────────┴───────────────────┘
```

**Left half — Electrical Tilt**, one heading per band, in this order:
`850 Tilt`, `900 Tilt`, `1800 Tilt 1`, `1800 Tilt 2`, `2100 Tilt`.

**Right half — Mechanical Tilt & Azimuth**:
`Antenna M Tilt`, `Antenna Azimuth Photo`, `Antenna Coverage Photo`.

The photo always sits **directly underneath its heading**. Every Post box is the
same size and on the same rows as the Pre photo facing it, so a pasted Post photo
lines up without any manual nudging.

### Rules the sheet follows

| Situation | What happens |
|---|---|
| A band has no photo | The heading still appears, above a red-tinted **"No Pre photo"** box. Every site's sheet keeps the same shape, so a genuinely missing photo is obvious. |
| A category has several photos | One heading, then every photo stacked beneath it. Nothing from the survey is dropped. |
| Photo is landscape | The box is made shorter to match, so the sheet doesn't stretch out with empty space. |
| Photo is portrait | The box grows taller, up to a cap. |
| A photo can't be read | A box saying so is drawn in its place, and the run reports it. The build never dies on one bad file. |

Photo categories the survey records but this sheet does not show — antenna label,
ports, RRU labels, TMA bracket, coverage location — are listed after each build
under *"not on sheet by design"*, so you can see they were found and deliberately
left out.

---

## Commands

### `build`

| Flag | Meaning |
|---|---|
| `--images-root DIR` | Folder holding one sub-folder per site. Required. |
| `--out DIR` | Where the workbooks are written. Required. |
| `--site NAME` | Build only this site. Repeatable. |
| `--max-dim N` | Downscale photos to this long edge (default `2400`). |
| `--full-res` | Embed photos at original resolution instead. |
| `--no-dedupe` | Keep a separate copy of every embedded image. |
| `--preview` | Also render a PNG of each sheet into `<out>/previews/`. |
| `--preview-sectors N` | Limit the preview to the first N sectors. |

### `report`

Prints what was found per site and sector without building anything — the fastest
way to spot a mis-named folder or a missing photo before you commit to a build.

### `web`

Opens the app. `--port` to change the port (default 8765), `--no-browser` to skip
opening a window.

---

## Why the photos come out smaller than the originals

Two deliberate steps, both reversible with flags:

**EXIF rotation is baked into the pixels.** Phone cameras record orientation in a
tag rather than rotating the image. Viewers honour that tag; **Excel does not**. A
portrait tilt photo embedded verbatim shows up on its side. The rotation is
applied to the pixels before embedding, so photos are always upright.

**Photos are downscaled to 2400 px on the long edge.** The source files are
full-resolution camera images; a site with 130 of them makes a workbook too large
to email. A tilt reading stays perfectly legible at this size. Use `--full-res`
to turn this off.

Identical images are also stored once and shared. The survey often records one
photograph against two bands — the 850 and 900 tilt shots are frequently the same
file — and this typically removes a third of the workbook's size without changing
anything on screen. Use `--no-dedupe` to turn it off.

---

## How it fits together

```
catalog.py    filenames  ->  (site, sector, category, sequence)
imaging.py    photo file ->  upright, right-sized, embeddable image
layout.py     the sheet's geometry: column bands, box sizes, row rhythm
plan.py       inventory + geometry -> every heading and box at a fixed coordinate
workbook.py   plan -> .xlsx
preview.py    plan -> .png          (same geometry, so the preview proves the sheet)
dedupe.py     shares identical embedded images
validate.py   structural checks: nothing overlaps, every drawing resolves
cli.py        command line
web/          the browser app: server.py (routes), jobs.py (background builds)
```

Planning is deliberately separate from writing. The Excel writer and the PNG
previewer walk the *same* `SheetPlan`, so a preview that looks right is evidence
the workbook is right rather than a second implementation that could drift.

---

## Adding a new photo category

1. Add the exact filename token to `catalog.py` — to `ELECTRICAL_TILT_CATEGORIES`
   or `MECHANICAL_AZIMUTH_CATEGORIES` to place it on the sheet, or to
   `OTHER_CATEGORIES` to have it recognised but left off.
2. Add its heading text to `DISPLAY_NAMES`.

Order in the list is the order on the sheet. Categories whose names end in a
digit are handled correctly — the parser matches against this vocabulary rather
than stripping trailing numbers, which is why `1800_Tilt_1_1` resolves to
category `1800_Tilt_1`, sequence `1`.

Categories the parser has never seen are still placed in the *report* output and
flagged as `UNKNOWN`, so a new survey token shows up rather than disappearing.

---

## Checking the output

Every build validates the file it just wrote — nothing overlaps, every drawing
resolves to an image that is present, no two merged ranges intersect (Excel
refuses to open a file with those). Problems are printed with a `!` and the
command exits non-zero.

### Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

Covers the filename-parsing edge cases, the geometry invariants (nothing
overlaps, nothing is clipped, Pre and Post stay aligned), an end-to-end build
that opens the resulting workbook and inspects its drawing layer, and negative
tests that deliberately corrupt a workbook to prove the validators actually
fire.

### Proving the right photo is under the right heading

```bash
python tools/verify_fidelity.py Documents/IMAGE-P202506262207_D001-20260 output
```

This shares no code with the builder. It re-parses each saved workbook from
scratch, matches every embedded picture back to a source photo **by comparing
pixels** — so downscaling and re-encoding cannot fool it — and then checks the
matched file's name agrees with the heading it was placed under. It also
confirms no picture ever landed in a Post band.

On the current photo set it verifies all 292 placed photos with no problems.

### A note on the survey data

Some photos are recorded under more than one name — a sector's 850 and 900 tilt
shots are often the same file, and a few sites reuse one azimuth photo across
sectors. The build places them as named, and reports how many copies it shared.
If that surprises you, it is a fact about the survey, not about the sheet.
