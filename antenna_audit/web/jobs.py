"""Run builds in the background and report progress to the browser.

A job owns a working directory holding one sub-folder per site, and an output
directory holding the finished workbooks.  Both live under a temporary root that
is removed when the job is discarded, so nothing accumulates on disk.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import catalog
from ..dedupe import deduplicate_media
from ..imaging import DEFAULT_MAX_DIM, ImagePreparer

DEFAULT_MODEL_DIR = Path("antenna_audit/classify/models").resolve()
from ..plan import build_plan
from ..validate import check_plan, check_workbook
from ..workbook import write_workbook

# Jobs older than this are cleaned up on the next sweep.
JOB_TTL_SECONDS = 6 * 60 * 60


@dataclass
class SiteResult:
    """Progress and outcome for one folder."""

    name: str
    state: str = "waiting"       # waiting | building | done | failed | skipped
    photos_found: int = 0
    photos_placed: int = 0
    post_placed: int = 0
    post_undecided: int = 0
    sectors: int = 0
    message: str = ""
    filename: str = ""
    size_bytes: int = 0
    missing: list[str] = field(default_factory=list)
    unplaced: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "photosFound": self.photos_found,
            "photosPlaced": self.photos_placed,
            "postPlaced": self.post_placed,
            "postUndecided": self.post_undecided,
            "sectors": self.sectors,
            "message": self.message,
            "filename": self.filename,
            "sizeBytes": self.size_bytes,
            "missing": self.missing,
            "unplaced": self.unplaced,
            "problems": self.problems,
        }


@dataclass
class Job:
    """One build run, covering every folder the user supplied at once."""

    id: str
    root: Path
    max_dim: int | None = DEFAULT_MAX_DIM
    model_dir: Path | None = None      # trained classifier + your confirmations
    state: str = "preparing"     # preparing | running | done | failed
    error: str = ""
    sites: dict[str, SiteResult] = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def uploads(self) -> Path:
        return self.root / "uploads"

    @property
    def post_uploads(self) -> Path:
        """Post photos, one sub-folder per site, each holding S1, S2, …"""
        return self.root / "post"

    @property
    def output(self) -> Path:
        return self.root / "output"

    def as_dict(self) -> dict:
        with self._lock:
            sites = [s.as_dict() for s in self.sites.values()]
        done = sum(1 for s in sites if s["state"] in ("done", "failed", "skipped"))
        return {
            "id": self.id,
            "state": self.state,
            "error": self.error,
            "sites": sites,
            "completed": done,
            "total": len(sites),
            "readyCount": sum(1 for s in sites if s["state"] == "done"),
        }


class JobStore:
    """Keeps jobs alive between requests and cleans up after them."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._root = Path(tempfile.mkdtemp(prefix="antenna-audit-web-"))

    def create(self, max_dim: int | None,
               model_dir: Path | None = None) -> Job:
        self.sweep()
        job_id = uuid.uuid4().hex[:12]
        root = self._root / job_id
        (root / "uploads").mkdir(parents=True, exist_ok=True)
        (root / "post").mkdir(parents=True, exist_ok=True)
        (root / "output").mkdir(parents=True, exist_ok=True)
        job = Job(id=job_id, root=root, max_dim=max_dim, model_dir=model_dir)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def sweep(self) -> None:
        """Drop jobs whose results nobody is coming back for."""
        cutoff = time.time() - JOB_TTL_SECONDS
        with self._lock:
            stale = [j for j in self._jobs.values() if j.created < cutoff]
            for job in stale:
                self._jobs.pop(job.id, None)
        for job in stale:
            shutil.rmtree(job.root, ignore_errors=True)

    def shutdown(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)


def run_job(job: Job) -> None:
    """Build every uploaded folder into its own workbook."""
    try:
        job.state = "running"
        site_dirs = sorted(
            (d for d in job.uploads.iterdir() if d.is_dir()), key=lambda d: d.name
        )
        # Register every site up front so the progress count is the real total
        # from the first poll, rather than growing as each one is discovered.
        for site_dir in site_dirs:
            job.sites.setdefault(site_dir.name, SiteResult(name=site_dir.name))

        for site_dir in site_dirs:
            result = job.sites[site_dir.name]
            result.state = "building"
            try:
                _build_one(job, site_dir, result)
            except Exception as exc:  # noqa: BLE001 - one bad folder must not stop the rest
                result.state = "failed"
                result.message = f"{type(exc).__name__}: {exc}"
        job.state = "done"
    except Exception as exc:  # noqa: BLE001
        job.state = "failed"
        job.error = f"{type(exc).__name__}: {exc}"


def _build_one(job: Job, site_dir: Path, result: SiteResult) -> None:
    inventory = catalog.scan_site(site_dir)
    result.photos_found = len(inventory.photos)

    if not inventory.sectors:
        result.state = "skipped"
        result.message = (
            "No antenna photos recognised in this folder. Check the file names "
            "still contain a sector, e.g. Ant_Sec_1__850_Tilt_1.jpg"
        )
        return

    preparer = ImagePreparer(job.root / "work" / site_dir.name, max_dim=job.max_dim)
    post_photos = _classify_post(job, site_dir.name, result)
    plan = build_plan(inventory, preparer, post_photos)
    layout_errors = check_plan(plan)

    filename = f"{site_dir.name} Antenna Audit Photos.xlsx"
    out_path = job.output / filename
    build = write_workbook(plan, out_path, preparer, unplaced=inventory.unplaced())
    deduplicate_media(out_path)

    report = check_workbook(out_path)
    problems = layout_errors + report.errors

    result.sectors = len(plan.sectors)
    result.photos_placed = build.photos_placed
    result.post_placed = plan.placed_post_photos
    result.filename = filename
    result.size_bytes = out_path.stat().st_size
    result.missing = list(plan.missing_slots)
    result.unplaced = dict(inventory.unplaced())
    result.problems = problems
    result.state = "done" if not problems else "failed"
    if problems:
        result.message = f"{len(problems)} structural problem(s) — do not use this file"
    for path, reason in build.failures:
        result.problems.append(f"unreadable: {path.name} ({reason})")


def _classify_post(job: Job, site: str, result: SiteResult):
    """Work out which Post photo belongs in which slot, if any were supplied.

    Slots the classifier cannot resolve are simply absent from the result, so
    they keep their empty drop box rather than carrying a guess.
    """
    site_dir = job.post_uploads / site
    if not site_dir.is_dir():
        return None

    from ..classify.bands import assign_site, to_post_photos
    from ..classify.overrides import OverrideStore
    from ..classify.predict import load_model
    from ..classify.store import ConfirmationStore
    from ..classify.train import MODEL_FILENAME

    model_dir = job.model_dir or DEFAULT_MODEL_DIR
    if not (model_dir / MODEL_FILENAME).exists():
        result.message = (
            "Post photos were supplied but no classifier has been trained yet. "
            "Run 'antenna-audit learn' on your finished workbooks first; the "
            "Post columns were left empty."
        )
        return None

    try:
        assignments = assign_site(
            site_dir, load_model(model_dir),
            ConfirmationStore(model_dir), overrides=OverrideStore(model_dir),
        )
    except Exception as exc:  # noqa: BLE001 - one bad site must not stop the run
        result.message = f"Post photos could not be classified: {exc}"
        return None

    result.post_undecided = sum(len(a.needs_decision) for a in assignments.values())
    return to_post_photos(assignments)


def bundle(job: Job) -> Path:
    """Zip every finished workbook so they can be downloaded in one go."""
    archive = job.root / "Antenna Audit Photos.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for site in job.sites.values():
            if site.state == "done" and site.filename:
                zf.write(job.output / site.filename, site.filename)
    return archive
