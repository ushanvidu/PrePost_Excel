"""Command line entry point."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from . import catalog
from .dedupe import deduplicate_media
from .imaging import DEFAULT_MAX_DIM, ImagePreparer
from .plan import build_plan
from .validate import check_plan, check_workbook
from .preview import render_preview
from .workbook import write_workbook


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size:.0f} B"
        size /= 1024
    return f"{size:.1f} GB"


def _select(inventories, only):
    if not only:
        return inventories
    wanted = {name.upper() for name in only}
    chosen = [inv for inv in inventories if inv.site.upper() in wanted]
    missing = wanted - {inv.site.upper() for inv in chosen}
    if missing:
        sys.exit(f"No image folder found for: {', '.join(sorted(missing))}")
    return chosen


def cmd_build(args: argparse.Namespace) -> int:
    images_root = Path(args.images_root).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    if not images_root.is_dir():
        sys.exit(f"Images root not found: {images_root}")

    inventories = _select(catalog.scan_root(images_root), args.site)
    if not inventories:
        sys.exit(f"No site folders found under {images_root}")

    out_dir.mkdir(parents=True, exist_ok=True)
    max_dim = None if args.full_res else args.max_dim
    work_root = Path(tempfile.mkdtemp(prefix="antenna-audit-"))
    total_placed = 0
    failures = 0

    try:
        for inventory in inventories:
            if not inventory.sectors:
                print(f"  {inventory.site:<10} skipped — no placeable photos found")
                continue

            preparer = ImagePreparer(work_root / inventory.site, max_dim=max_dim)
            plan = build_plan(inventory, preparer)
            out_path = out_dir / f"{inventory.site} Antenna Audit Photos.xlsx"

            layout_errors = check_plan(plan)

            result = write_workbook(
                plan, out_path, preparer, unplaced=inventory.unplaced()
            )
            saved = ""
            if not args.no_dedupe:
                dedupe = deduplicate_media(out_path)
                if dedupe.saved_bytes > 0:
                    saved = (
                        f", deduped {dedupe.parts_before - dedupe.parts_after}"
                        f" copies (-{_human(dedupe.saved_bytes)})"
                    )

            # Verify the file that was actually written, after de-duplication.
            report = check_workbook(out_path)
            problems = layout_errors + report.errors

            size = _human(out_path.stat().st_size)
            print(
                f"  {inventory.site:<10} {len(plan.sectors)} sectors, "
                f"{result.photos_placed:>3} photos, {plan.total_rows:>4} rows, "
                f"{size}{saved}"
            )
            if problems:
                failures += len(problems)
                print(f"             ! {len(problems)} STRUCTURAL PROBLEM(S):")
                for problem in problems[:5]:
                    print(f"               - {problem}")
                if len(problems) > 5:
                    print(f"               … and {len(problems) - 5} more")
            if result.missing_slots:
                print(
                    f"             {len(result.missing_slots)} empty Pre slot(s): "
                    + "; ".join(result.missing_slots[:3])
                    + (" …" if len(result.missing_slots) > 3 else "")
                )
            if result.photos_unplaced:
                summary = ", ".join(
                    f"{k} x{v}" for k, v in list(result.photos_unplaced.items())[:4]
                )
                print(f"             not on sheet by design: {summary}")
            for path, reason in result.failures:
                print(f"             ! unreadable: {path.name} ({reason})")
                failures += 1

            if args.preview:
                png = out_dir / "previews" / f"{inventory.site}.png"
                render_preview(plan, png, preparer, max_sectors=args.preview_sectors)
                print(f"             preview: {png}")

            total_placed += result.photos_placed
    finally:
        shutil.rmtree(work_root, ignore_errors=True)

    print(f"\nDone. {total_placed} photos placed across "
          f"{len(inventories)} site(s) into {out_dir}")
    if failures:
        print(f"Finished with {failures} problem(s) — see the lines marked ! above.")
    return 1 if failures else 0


def cmd_report(args: argparse.Namespace) -> int:
    images_root = Path(args.images_root).expanduser().resolve()
    inventories = _select(catalog.scan_root(images_root), args.site)

    for inventory in inventories:
        print(f"\n{inventory.site}  ({len(inventory.photos)} photos)")
        for sector in inventory.sectors:
            print(f"  Sector {sector}")
            for group, names in (
                ("electrical", catalog.ELECTRICAL_TILT_CATEGORIES),
                ("mechanical", catalog.MECHANICAL_AZIMUTH_CATEGORIES),
            ):
                cells = []
                for name in names:
                    count = len(inventory.get(sector, name))
                    cells.append(f"{name}={count or '-'}")
                print(f"    {group:<11}{'  '.join(cells)}")
        unplaced = inventory.unplaced()
        if unplaced:
            print("  not on sheet: "
                  + ", ".join(f"{k} x{v}" for k, v in unplaced.items()))
        unknown = inventory.unknown_categories()
        if unknown:
            print(f"  UNKNOWN categories: {', '.join(sorted(unknown))}")
        if inventory.skipped:
            print(f"  non-image files skipped: {len(inventory.skipped)}")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    from .web.server import serve

    serve(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="antenna-audit",
        description="Build one Antenna Audit Photos workbook per site, "
                    "with survey (Pre) photos placed under their headings.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="build the workbooks")
    build.add_argument("--images-root", required=True,
                       help="folder containing one sub-folder per site")
    build.add_argument("--out", required=True, help="output folder for .xlsx files")
    build.add_argument("--site", action="append",
                       help="only build this site (repeatable)")
    build.add_argument("--max-dim", type=int, default=DEFAULT_MAX_DIM,
                       help=f"downscale photos to this long edge "
                            f"(default {DEFAULT_MAX_DIM})")
    build.add_argument("--full-res", action="store_true",
                       help="embed photos at original resolution")
    build.add_argument("--no-dedupe", action="store_true",
                       help="keep a separate copy of every embedded image")
    build.add_argument("--preview", action="store_true",
                       help="also render a PNG preview of each sheet")
    build.add_argument("--preview-sectors", type=int, default=None,
                       help="limit the preview to the first N sectors")
    build.set_defaults(func=cmd_build)

    report = sub.add_parser("report", help="show what photos were found")
    report.add_argument("--images-root", required=True)
    report.add_argument("--site", action="append")
    report.set_defaults(func=cmd_report)

    web = sub.add_parser("web", help="open the app in a browser")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--no-browser", action="store_true",
                     help="do not open a browser window automatically")
    web.set_defaults(func=cmd_web)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
