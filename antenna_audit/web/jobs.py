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
    state: str = "preparing"     # preparing | running | done | failed
    error: str = ""
    sites: dict[str, SiteResult] = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def uploads(self) -> Path:
        return self.root / "uploads"

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

    def create(self, max_dim: int | None) -> Job:
        self.sweep()
        job_id = uuid.uuid4().hex[:12]
        root = self._root / job_id
        (root / "uploads").mkdir(parents=True, exist_ok=True)
        (root / "output").mkdir(parents=True, exist_ok=True)
        job = Job(id=job_id, root=root, max_dim=max_dim)
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
        for site_dir in site_dirs:
            result = job.sites.setdefault(site_dir.name, SiteResult(name=site_dir.name))
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
    plan = build_plan(inventory, preparer)
    layout_errors = check_plan(plan)

    filename = f"{site_dir.name} Antenna Audit Photos.xlsx"
    out_path = job.output / filename
    build = write_workbook(plan, out_path, preparer, unplaced=inventory.unplaced())
    deduplicate_media(out_path)

    report = check_workbook(out_path)
    problems = layout_errors + report.errors

    result.sectors = len(plan.sectors)
    result.photos_placed = build.photos_placed
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


def bundle(job: Job) -> Path:
    """Zip every finished workbook so they can be downloaded in one go."""
    archive = job.root / "Antenna Audit Photos.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for site in job.sites.values():
            if site.state == "done" and site.filename:
                zf.write(job.output / site.filename, site.filename)
    return archive
