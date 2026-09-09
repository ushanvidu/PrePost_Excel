"""The review screen: confirm where the Post photos go.

Two jobs, and the second one is the reason this exists.

*Filling the slots the classifier cannot.* 1800-1, 1800-2 and 2100 sit on
electrically identical arrays, so nothing in the photograph says which is which.
Those slots arrive empty with their candidates ranked, and the engineer assigns
them.

*Teaching the classifier.* Every confirmation is stored and folded into the next
fit. That matters because the survey crew changed instruments between rounds —
the Pre photos show a green Digi-Pas, the Post photos a blue angle gauge — so a
model trained on Pre photos alone picks the right mechanical-tilt shot in only
half the sectors. With a couple of sectors confirmed it gets all of them. The
clicks are not overhead; they are the training signal.
"""

from __future__ import annotations

import io
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file
from PIL import Image, ImageOps

from ..catalog import heading_for
from ..classify import labels
from ..classify.bands import ALL_ROWS, assign_site
from ..classify.predict import load_model
from ..classify.store import ConfirmationStore
from ..classify.train import MODEL_FILENAME, train as train_model

THUMB_LONG_EDGE = 320

bp = Blueprint("review", __name__)

# Folders the current session is allowed to read thumbnails from. The thumbnail
# route takes a path, so it must never serve anything outside a folder the user
# has explicitly pointed the app at.
_allowed_roots: set[Path] = set()


def _permitted(path: Path) -> bool:
    resolved = path.resolve()
    return any(
        resolved == root or root in resolved.parents for root in _allowed_roots
    )


@bp.get("/review")
def review_page():
    return render_template("review.html")


@bp.post("/api/review/scan")
def scan():
    """Classify a site's Post photos and return every slot with its candidates."""
    payload = request.get_json(silent=True) or {}
    post_path = (payload.get("postPath") or "").strip()
    images_root = (payload.get("imagesRoot") or "").strip()
    model_dir = Path(payload.get("modelDir")
                     or "antenna_audit/classify/models").expanduser().resolve()

    if not post_path:
        return jsonify(error="Enter the folder holding this site's Post photos."), 400
    site_dir = Path(post_path).expanduser().resolve()
    if not site_dir.is_dir():
        return jsonify(error=f"Not a folder: {site_dir}"), 400

    store = ConfirmationStore(model_dir)

    if not (model_dir / MODEL_FILENAME).exists():
        if not images_root:
            return jsonify(
                error="No trained classifier yet. Give the labelled Pre photos "
                      "folder so one can be trained."
            ), 400
        root = Path(images_root).expanduser().resolve()
        if not root.is_dir():
            return jsonify(error=f"Not a folder: {root}"), 400
        try:
            train_model(root, model_dir,
                        confirmed=store.training_rows([site_dir, root]))
        except ValueError as exc:
            # Nearly always the wrong folder: the trainer needs the *labelled
            # Pre* photos, whose filenames carry the sector and category.
            return jsonify(error=(
                f"{exc}. That folder needs the labelled Pre photos — one "
                f"sub-folder per site, with names like "
                f"'..._Ant_Sec_1__850_Tilt_1.jpg'. Point it at the survey "
                f"export, not at the Post photos."
            )), 400

    _allowed_roots.add(site_dir)
    assignments = assign_site(site_dir, load_model(model_dir), store)

    sectors = []
    for name, assignment in sorted(assignments.items()):
        number = int("".join(ch for ch in name if ch.isdigit()) or 0)
        slots = []
        for row in ALL_ROWS:
            slot = assignment.slots.get(row)
            if slot is None:
                continue
            slots.append({
                "row": row,
                "heading": heading_for(row, number),
                "photo": str(slot.photo) if slot.photo else None,
                "photoName": slot.photo.name if slot.photo else None,
                "confidence": round(slot.confidence, 3),
                "confirmed": slot.confirmed,
                "needsDecision": slot.needs_decision,
                "reasons": slot.reasons,
                "alternatives": [
                    {"path": str(p), "name": p.name} for p in slot.alternatives
                ],
            })
        sectors.append({
            "sector": name,
            "slots": slots,
            "unassigned": [
                {"path": str(p), "name": p.name} for p in assignment.unassigned
            ],
        })

    return jsonify(
        site=site_dir.name,
        sectors=sectors,
        confirmations=len(store),
        modelDir=str(model_dir),
    )


@bp.get("/api/review/thumb")
def thumbnail():
    """Serve a small preview of one photo, EXIF-rotated."""
    raw = request.args.get("path", "")
    if not raw:
        return jsonify(error="missing path"), 400
    path = Path(raw)
    if not path.is_file() or not _permitted(path):
        return jsonify(error="not found"), 404

    with Image.open(path) as img:
        img.load()
        oriented = ImageOps.exif_transpose(img).convert("RGB")
        oriented.thumbnail((THUMB_LONG_EDGE, THUMB_LONG_EDGE), Image.LANCZOS)
        buffer = io.BytesIO()
        oriented.save(buffer, format="JPEG", quality=82)
    buffer.seek(0)
    return send_file(buffer, mimetype="image/jpeg")


@bp.post("/api/review/confirm")
def confirm():
    """Record one decision: this photo belongs in this slot."""
    payload = request.get_json(silent=True) or {}
    raw = (payload.get("path") or "").strip()
    row = (payload.get("row") or "").strip()
    site = (payload.get("site") or "").strip()
    sector = (payload.get("sector") or "").strip()
    model_dir = Path(payload.get("modelDir")
                     or "antenna_audit/classify/models").expanduser().resolve()

    path = Path(raw)
    if not raw or not path.is_file() or not _permitted(path):
        return jsonify(error="unknown photo"), 400
    if row not in ALL_ROWS:
        return jsonify(error=f"unknown slot '{row}'"), 400

    # The visual class the classifier learns from, and the sheet row the
    # assignment step reads back. Tilt rows share one visual class, so the row
    # is kept separately in `port`.
    category = labels.visual_class(row)

    store = ConfirmationStore(model_dir)
    store.add(path, site=site, sector=sector, category=category, port=row)
    store.save()
    return jsonify(ok=True, confirmations=len(store))


@bp.post("/api/review/retrain")
def retrain():
    """Refit the classifier including every confirmation made so far."""
    payload = request.get_json(silent=True) or {}
    images_root = Path((payload.get("imagesRoot") or "")).expanduser().resolve()
    model_dir = Path(payload.get("modelDir")
                     or "antenna_audit/classify/models").expanduser().resolve()
    if not images_root.is_dir():
        return jsonify(error="Give the labelled Pre photos folder to retrain."), 400

    store = ConfirmationStore(model_dir)
    roots = list(_allowed_roots) + [images_root]
    scores = train_model(images_root, model_dir,
                         confirmed=store.training_rows(roots))
    return jsonify(
        ok=True,
        images=scores["n_images"],
        confirmedUsed=scores["confirmed_used"],
        accuracy=round(scores["overall_accuracy"], 3),
    )
