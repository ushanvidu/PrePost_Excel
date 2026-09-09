"""A small local web app for building the workbooks without the command line.

Two ways in, because the folders are large:

*Upload* copies the photos into the browser and up to the server.  It works from
any machine but moves every byte twice, so a 100 MB site folder takes a moment.

*Folder on this computer* takes a path and reads it in place.  Nothing is
copied, so it is effectively instant — the right choice when the photos are
already sitting on the machine running the app.
"""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

from flask import (
    Flask, jsonify, render_template, request, send_file, abort,
)
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from ..imaging import DEFAULT_MAX_DIM
from .jobs import JobStore, bundle, run_job

# Photo sets run to hundreds of megabytes; the default 16 MB cap would reject
# every real upload.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024 * 1024


def _save_folder_upload(files, destination: Path, keep_sector: bool = False) -> int:
    """Write a browser folder upload to disk, preserving the folder shape.

    The browser sends each file as "Parent/SITE/photo.jpg". For Pre photos the
    folder holding the photo is the site. Post photos are one level deeper —
    "Parent/SITE/S1/photo.jpg" — because they are grouped by sector, so that
    level is kept.
    """
    saved = 0
    for storage in files:
        relative = (storage.filename or "").replace("\\", "/")
        parts = [p for p in relative.split("/") if p not in ("", ".", "..")]
        needed = 3 if keep_sector else 2
        if len(parts) < needed:
            continue
        name = secure_filename(parts[-1])
        if not name or name.startswith("."):
            continue
        if keep_sector:
            target = destination / _safe_site_name(parts[-3]) / _safe_site_name(parts[-2])
        else:
            target = destination / _safe_site_name(parts[-2])
        target.mkdir(parents=True, exist_ok=True)
        storage.save(target / name)
        saved += 1
    return saved


def _safe_site_name(raw: str) -> str:
    """Turn a folder name into something safe to use as a file name."""
    cleaned = secure_filename(raw.strip()) or "Site"
    return cleaned[:60]


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    store = JobStore()
    app.extensions["job_store"] = store

    from .review import bp as review_bp
    app.register_blueprint(review_bp)

    @app.get("/")
    def index():
        return render_template("index.html", default_max_dim=DEFAULT_MAX_DIM)

    @app.post("/api/build")
    def start_build():
        """Accept uploaded folders and start a build."""
        max_dim = _read_max_dim(request.form.get("maxDim"))
        job = store.create(max_dim)

        uploaded = request.files.getlist("files")
        if not uploaded:
            return jsonify(error="No files were received."), 400

        saved = _save_folder_upload(uploaded, job.uploads)
        # Post photos are optional; when present they arrive under their own
        # field so the two sets never get mixed up.
        _save_folder_upload(request.files.getlist("postFiles"), job.post_uploads,
                            keep_sector=True)
            # The browser sends "Parent/SITE/photo.jpg"; the folder immediately
            # containing the photo is the site, which works whether the user
            # picked one site folder or a parent holding many.
        if not saved:
            return jsonify(
                error="No usable image files were found. Drop the folder itself, "
                      "not the photos inside it."
            ), 400

        _start(job)
        return jsonify(job.as_dict())

    @app.post("/api/build-local")
    def start_local_build():
        """Build from a folder already on this machine, without copying it."""
        payload = request.get_json(silent=True) or {}
        raw_path = (payload.get("path") or "").strip()
        post_raw = (payload.get("postPath") or "").strip()
        if not raw_path:
            return jsonify(error="Enter a folder path."), 400

        root = Path(raw_path).expanduser()
        if not root.is_dir():
            return jsonify(error=f"Not a folder: {root}"), 400

        children = sorted(
            d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")
        )
        # A folder of site folders, or a single site folder holding photos.
        site_dirs = children or [root]

        job = store.create(_read_max_dim(payload.get("maxDim")))
        linked = 0
        for site_dir in site_dirs:
            images = [
                f for f in site_dir.iterdir()
                if f.is_file() and not f.name.startswith(".")
            ]
            if not images:
                continue
            target = job.uploads / _safe_site_name(site_dir.name)
            target.mkdir(parents=True, exist_ok=True)
            for image in images:
                # Hard-link where possible so nothing is copied.
                destination = target / image.name
                try:
                    os.link(image, destination)
                except OSError:
                    shutil.copy2(image, destination)
            linked += 1

        if not linked:
            return jsonify(
                error=f"No photos found in {root} or its sub-folders."
            ), 400

        if post_raw:
            post_root = Path(post_raw).expanduser()
            if not post_root.is_dir():
                return jsonify(error=f"Not a folder: {post_root}"), 400
            matched = _link_post_tree(post_root, job.post_uploads)
            if not matched:
                return jsonify(error=(
                    f"No Post photos found under {post_root}. It should hold one "
                    f"folder per site, each containing S1, S2, … sector folders."
                )), 400

        _start(job)
        return jsonify(job.as_dict())

    def _link_post_tree(source: Path, destination: Path) -> int:
        """Mirror a Post tree (site/sector/photos) without copying the photos."""
        linked = 0
        for site_dir in sorted(d for d in source.iterdir()
                               if d.is_dir() and not d.name.startswith(".")):
            for sector_dir in sorted(d for d in site_dir.iterdir()
                                     if d.is_dir() and not d.name.startswith(".")):
                photos = [f for f in sector_dir.iterdir()
                          if f.is_file() and not f.name.startswith(".")]
                if not photos:
                    continue
                target = (destination / _safe_site_name(site_dir.name)
                          / _safe_site_name(sector_dir.name))
                target.mkdir(parents=True, exist_ok=True)
                for photo in photos:
                    try:
                        os.link(photo, target / photo.name)
                    except OSError:
                        shutil.copy2(photo, target / photo.name)
                    linked += 1
        return linked

    @app.get("/api/job/<job_id>")
    def job_status(job_id: str):
        job = store.get(job_id)
        if job is None:
            return jsonify(error="This build has expired."), 404
        return jsonify(job.as_dict())

    @app.get("/api/job/<job_id>/file/<path:filename>")
    def download_one(job_id: str, filename: str):
        job = store.get(job_id)
        if job is None:
            abort(404)
        path = (job.output / filename).resolve()
        if job.output.resolve() not in path.parents or not path.is_file():
            abort(404)
        return send_file(path, as_attachment=True, download_name=path.name)

    @app.get("/api/job/<job_id>/all")
    def download_all(job_id: str):
        job = store.get(job_id)
        if job is None:
            abort(404)
        archive = bundle(job)
        return send_file(
            archive, as_attachment=True, download_name="Antenna Audit Photos.zip"
        )

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(
            error="That upload is larger than 8 GB. Use the "
                  "“folder on this computer” option instead."
        ), 413

    # The browser calls these endpoints with fetch() and reads the reply as
    # JSON. Flask's default error page is HTML, so any unhandled failure used to
    # surface in the console as `Unexpected token '<', "<!doctype "...` — which
    # says nothing about what actually went wrong. Answer in JSON instead, and
    # carry the real reason through to the screen.
    def _wants_json() -> bool:
        return request.path.startswith("/api/")

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException):
        if not _wants_json():
            return error
        return jsonify(error=error.description or error.name), error.code

    @app.errorhandler(Exception)
    def unhandled_error(error: Exception):
        if not _wants_json():
            raise error
        app.logger.exception("unhandled error on %s", request.path)
        return jsonify(
            error=f"{type(error).__name__}: {error}",
            hint="This is a bug or a bad input path. The server log has the "
                 "full traceback.",
        ), 500

    def _start(job) -> None:
        thread = threading.Thread(target=run_job, args=(job,), daemon=True)
        thread.start()

    return app


def _read_max_dim(value) -> int | None:
    """`0` or blank means embed at full resolution."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_DIM
    return None if number <= 0 else number


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    app = create_app()
    url = f"http://{host}:{port}/"
    print(f"\n  Antenna Audit Photo Sheets\n  {url}\n  Press Ctrl+C to stop.\n")
    if open_browser:
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=host, port=port, threaded=True, debug=False)
