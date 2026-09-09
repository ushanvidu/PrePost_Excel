# Antenna Audit Photo Sheets

Builds one Excel workbook per site from a folder of field-survey photos, placing
every **Pre** photo under its heading and leaving aligned, labelled empty boxes
for the **Post** photos you paste in by hand afterwards.

It also classifies the unlabelled **Post** photos and places the ones it can
resolve, leaving the rest for you to confirm in a review screen.

The sheet layout was reverse-engineered from an existing hand-made workbook. That
workbook and the survey photos are field data and are **not** in this repository —
you bring your own.

Everything runs on your own machine. There is no API key, no account, and no
cloud service — the photos never leave your laptop.

---

## Install

### What you need

* **Python 3.10 or newer** — check with `python3 --version`. Verified on 3.12 and
  3.14; a fresh clone installs and passes its full test suite on both. If you don't
  have Python, get it from [python.org](https://www.python.org/downloads/) or, on a
  Mac with Homebrew, `brew install python`.
* **Git** — to clone the repository.

Nothing else. No database, no API keys, no system libraries to compile.

### Setup

```bash
git clone https://github.com/ushanvidu/PrePost_Excel.git
cd PrePost_Excel

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

That's it. The first install pulls a few scientific packages (OpenCV, scikit-learn,
NumPy) and takes a couple of minutes; after that startup is instant.

### Check it works

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

You should see **81 tests pass**. They build their own synthetic images, so this
works on a clean checkout before you have added any photos of your own.

### Run it

```bash
python -m antenna_audit web
```

Then open **http://127.0.0.1:8765** in a browser. On macOS you can instead
double-click **`Antenna Audit App.command`**, which sets up the virtual
environment on first run and then opens the browser for you.

### Where to put your photos

The tool expects one folder per site. Pre photos keep the names the survey tool
gives them; Post photos can be named anything, but live in per-sector folders:

```
Pre photos                          Post photos
IMAGE-P20250626.../                 Pre_Post/
├── GMTHI1/                         └── GMNIT1/
│   ├── ..._Ant_Sec_1__850_Tilt_1.jpg   ├── S1/
│   └── ..._Ant_Sec_1_Antenna_M_Tilt_1.jpg  │   └── WhatsApp Image ....jpeg
└── GMNIT1/                             ├── S2/
    └── ...                             ├── S3/
                                        └── S4/
```

Nothing in the repository depends on those exact folder names — you point the app
at whichever folders you have.

---

## The app

Double-click **`Antenna Audit App.command`**. It sets itself up the first time,
then opens in your browser.

Or from a terminal:

```bash
python -m antenna_audit web
```

Give it your **Pre** photos and, if you have them, your **Post** photos. Each site
becomes one finished workbook you can download — Pre photos under their headings,
Post photos in the Post columns. Download them one at a time or all as a `.zip`.

The Post folder is optional: leave it out and you get a sheet with blank Post
boxes to fill in by hand, exactly as before.

### Two ways to give it folders

| | When to use it |
|---|---|
| **Upload folders** | Drag folders onto the drop zones — one for Pre, one for Post. Works from any machine, but every photo is copied into the browser and up to the app, so a large site folder takes a moment. |
| **Folder on this computer** | Type the two paths. The photos are read straight from disk with nothing copied, so it is close to instant. Use this when the photos are already on the machine running the app. |

The two folders are shaped differently, because the photos arrive differently:

```
Pre  — the folder holding the photos is the site
       IMAGE-P20250626.../ GMTHI1/ ..._Ant_Sec_1__850_Tilt_1.jpg

Post — grouped by sector, so there is one level more
       Pre_Post/ GMNIT1/ S1/ WhatsApp Image ....jpeg
                        S2/ ...
```

Each site card shows how many Pre and Post photos landed, and how many Post slots
were left empty for you. Those are the ones the classifier would only have been
guessing at — open the review screen, settle them, and build again.

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
classify/     Post-photo classifier: features.py (colour/shape/texture),
              train.py (fit + honest scoring), predict.py (ranked candidates),
              store.py (your confirmations, fed back into training)
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

## Classifying the Post photos

Post photos arrive with no useful names (`WhatsApp Image 2026-09-06 at 19.37.45.jpeg`)
in per-sector folders. `classify` ranks them against the sheet's slots:

```bash
python -m antenna_audit classify /path/to/GMNIT1 \
    --images-root Documents/IMAGE-P202506262207_D001-20260
```

It prints, per sector, the best photo for each single-slot category with a confidence,
plus alternatives, plus the electrical-tilt photos it found. Everything runs on your
machine — no API, no cost, and no client site imagery leaving the laptop.

### Two things the photos genuinely cannot tell you

Worth knowing before trusting any tool, including this one. The antenna is a Huawei
**AQU4518R9v06**: `690-960 / 1695-2690 / 1695-2690 / 1695-2690` — one low-band array and
three identical high-band arrays, on **four** RET adjusters.

- **850 and 900 share one array and one adjuster.** There is one photo, not two. The Pre
  data proves it: those files are byte-identical under both names. The sheet places the
  same photo in both rows, which is what the Pre side already did.
- **1800-1 / 1800-2 / 2100 sit on three electrically identical arrays.** Which band feeds
  which port is set by RRU cabling and leaves no visual trace. No classifier can read it
  off the photo; you assign it once per site and the tool remembers.

So the tool classifies to *category*, and identifies the *port* — never the band directly.

**What was tried and rejected on measurement**, recorded so nobody repeats it:

| Approach | Result |
|---|---|
| Red-ring detection (finds the low-band adjuster) | **Kept** — 9/13 per photo, but 4/4 when picking the best ring photo per sector, which is all that is needed |
| OCR of the moulded port codes | **Removed** — Tesseract 5.5.3 recovered a correct code in **0 of 12** frames, including the one showing all four codes at once. Moulded, upside-down, corroded, low-contrast text at 1.2 MP is out of reach |
| Shooting order as a band hint | **Rejected** — measured across four sectors, it is ad-hoc |
| Training a CNN | **Rejected** — 148 labelled tilt photos, 15 of them duplicate mislabels, for a label that is not in the pixels |

### Placing them in the sheet

```bash
python -m antenna_audit build --images-root <pre-photos> --out output \
    --post-root /path/to/Pre_Post
```

Post photos the classifier resolves are placed automatically; anything it cannot
resolve keeps its empty drop box, so an unresolved slot reads as "fill this in"
rather than silently carrying a wrong photo.

### The review screen

```bash
python -m antenna_audit web        # then open /review
```

Each slot shows its proposed photo, the confidence, the reason, and the runner-up
candidates as thumbnails. Click a thumbnail to put that photo in that slot. On the
measured site, of the 12 single-slot picks: **8 correct, 1 wrong, 3 deferred to you**
— the deferrals are slots where the best candidate scored below 30%, which is where
every mistake fell.

### Wrong photo? Use your own

Every slot has **"Wrong photo? Use one from my computer…"**. Pick any image and it
takes that slot outright — no ranking, no gate, because you looked at it and said
so. A slot you have replaced shows **your photo** and an undo link that puts the
app's pick back.

Your original file is **copied, never moved**; the app only ever deletes copies it
made itself, and only the one for the slot you are replacing.

### What each slot will accept

Ranking alone put a cable-tag close-up under Mechanical Tilt and a tilt photo under
Azimuth, so the two instrument slots have a rule on top of the ranking:

| Slot | Should show |
|---|---|
| **Azimuth** | a **compass** — a round dial |
| **Mechanical tilt** | the **meter with a reading on it** — a display with digits |

These started as hard rules that refused any photo failing them. Measured against
the photos engineers had actually placed, that version accepted 15% of real
compasses and **none** of the real meters — the thresholds had been tuned on
synthetic test images, not reality. Loosening them enough to admit the real ones
made them accept almost everything.

So the evidence now feeds the classifier as a signal rather than a veto, and it is
trained on real Post photos. The detectors survive as the readable half: the review
screen shows *why* a photo looked like a compass or a display. Both are
colour-blind on purpose — the crew has used a green Digi-Pas, a blue angle gauge
and a SHAHE inclinometer, so anything keyed to colour fails on the next one.

### Teach it from workbooks you have already finished

A completed sheet is labelled training data that costs nobody extra work: every
Post photo in it sits under a heading a person chose, and the image is stored
inside the file.

```bash
python -m antenna_audit learn /path/to/Completed \
    --images-root Documents/IMAGE-P202506262207_D001-20260
```

Reading seven finished workbooks gave 157 labelled Post photos and took the
mechanical-tilt pick from 2 sectors in 4 to 22 in 25. Full figures, including
what was tried and rejected, are in [docs/accuracy.md](docs/accuracy.md).

Measured leave-one-site-out, so every site is predicted by a model that never saw
it: **95% of placements correct** (53 right, 3 wrong), with 19 slots deferred to
you rather than guessed.

### It learns from your confirmations

The survey crew changed instruments between rounds: the Pre photos show a green
**Digi-Pas** inclinometer, the Post photos a blue **angle gauge**. A model trained only on
Pre photos therefore learns the wrong thing and does poorly on the Post mechanical-tilt
and azimuth shots.

Confirming photos fixes it, and the confirmations are stored and folded into the next fit:

| Trained on | Per-photo accuracy on Post photos | Correct top pick per sector |
|---|---|---|
| Pre photos only | 62% | mech 2/4 · azimuth 2/4 · coverage 4/4 |
| Pre + confirmed Post photos | **84%** | mech **4/4** · azimuth **4/4** · coverage **4/4** |

Measured leave-one-sector-out — every number is from sectors the model had never seen.
Confirming two sectors took the remaining two from 4/6 correct to **6/6**. Electrical
tilt, the category that matters most, is **13/14** with no confirmations at all.

Confirmations are keyed by image content, so re-sending the same photo under a new
WhatsApp name still counts as answered.

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
