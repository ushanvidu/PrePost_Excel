# Measured accuracy

Every figure here is out-of-sample: **leave-one-site-out**, so each site is
predicted by a model that never saw any photo from it. Measured against 157
distinct Post photos that engineers placed by hand in seven completed workbooks.

## Placing a photo in a slot

| Slot | Correct | Wrong | Deferred to you | Accuracy when it placed |
|---|---:|---:|---:|---:|
| Mechanical tilt | 14 | 1 | 10 | **93%** |
| Azimuth | 21 | 1 | 3 | **95%** |
| Coverage | 18 | 1 | 6 | **95%** |
| **Overall** | **53** | **3** | **19** | **95%** |

"Deferred" means the slot was left empty on purpose and shown for a decision,
because the best candidate scored below the confidence floor. That is the design:
an empty slot with a reason beats a confident wrong answer in an audit document.

Ranking alone, ignoring the confidence floor, the right photo comes top of its
column in 22/25 sectors for mechanical tilt, 24/25 for azimuth and 19/20 for
coverage.

## What moved the numbers

**Training on completed workbooks.** The survey crew changed instruments between
rounds — a green Digi-Pas inclinometer in the Pre photos, a blue angle gauge and
a SHAHE inclinometer in the Post — so a model trained on Pre photos alone is
looking for the wrong object. It picked the right mechanical-tilt photo in 2
sectors out of 4. Feeding it the Post photos out of finished workbooks took that
to 22 of 25.

**Instrument evidence as features, not gates.** Azimuth should show a compass and
mechanical tilt a meter with a reading. Implemented first as hard gates with
thresholds picked by eye on synthetic images, they were measured against the real
placements and failed badly in both directions:

| Version | Accepted real compasses | Accepted real meters | Wrongly accepted others |
|---|---:|---:|---:|
| Strict thresholds | 15% | 0% | ~0% |
| Loosened to admit real ones | 100% | 100% | 74% / 100% |

Neither is usable as a veto. The same signals now feed the classifier as
features, where they are weighed against everything else and trained on real
photos. The detectors remain as the readable half: they say *why* a photo looks
like a compass or a display, and the review screen shows that reason.

## What is not measured here

The band rows — 850, 900, 1800-1, 1800-2, 2100 — are not a classification
problem and no accuracy is claimed for them. 850 and 900 share one array and one
adjuster, so there is one photograph for both rows. The three high-band arrays
are electrically identical, so which band feeds which port comes from the RRU
cabling and leaves no trace in the image. Those slots are assigned by hand.

## Reproducing

```bash
python -m antenna_audit learn <completed-workbooks-folder> \
    --images-root <labelled-pre-photos>
```

This reads the placements out of the workbooks, folds them into training, and
prints leave-sites-out accuracy per class.
