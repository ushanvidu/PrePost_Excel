# Measurement records

These CSVs are not test fixtures — nothing in `tests/` reads them, and the suite
passes without the photos they describe.

They record ground truth established by looking at every photo of one site, and
they are what the accuracy figures in the top-level README were measured against:

* `gmnit1_post_labels.csv` — the category of all 60 Post photos of GMNIT1. The
  classifier trains only on the labelled Pre photos, so this is a genuine
  out-of-sample test set.
* `gmnit1_red_ring.csv` — which electrical-tilt photos show the red-painted ring
  on the low-band connector, used to score the ring detector.

Keep them if you want to re-measure after changing the features; they are the
only labelled Post data that exists.
