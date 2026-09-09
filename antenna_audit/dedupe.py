"""Collapse identical embedded images into a single media part.

The survey frequently records one photograph against two bands — the 850 and
900 tilt shots are usually the same file — and openpyxl embeds a separate copy
of the image for every placement.  Rewriting the drawing relationships to share
one copy typically removes a third of the workbook's size without changing a
single pixel of what Excel displays.

Rewriting is deliberately conservative.  Only relationships of the *image* type
that resolve to a part inside ``xl/media`` are touched, and targets are resolved
to full part paths before being compared, so a hyperlink or an unrelated part
that happens to end in the same filename is never rewritten.
"""

from __future__ import annotations

import hashlib
import posixpath
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_IMAGE_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"


@dataclass
class DedupeResult:
    parts_before: int
    parts_after: int
    bytes_before: int
    bytes_after: int

    @property
    def saved_bytes(self) -> int:
        return self.bytes_before - self.bytes_after


def _resolve(target: str, part_dir: str) -> str:
    """Resolve a relationship target to a full part path within the package."""
    if target.startswith("/"):
        return target[1:]
    return posixpath.normpath(posixpath.join(part_dir, target))


def _relativise(part: str, part_dir: str) -> str:
    """Express a part path the same way openpyxl writes it: absolute."""
    return "/" + part


def deduplicate_media(path: Path) -> DedupeResult:
    """Rewrite ``path`` in place so identical media parts are stored once."""
    original_size = path.stat().st_size

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        media = [n for n in names if n.startswith("xl/media/")]

        canonical: dict[str, str] = {}    # digest -> part kept
        replacement: dict[str, str] = {}  # part dropped -> part kept
        for name in sorted(media):
            digest = hashlib.sha256(zf.read(name)).hexdigest()
            keeper = canonical.setdefault(digest, name)
            if keeper != name:
                replacement[name] = keeper

        if not replacement:
            return DedupeResult(len(media), len(media), original_size, original_size)

        entries: list[tuple[zipfile.ZipInfo, bytes]] = []
        for info in zf.infolist():
            if info.filename in replacement:
                continue  # duplicate media part: dropped
            data = zf.read(info.filename)
            if info.filename.endswith(".rels"):
                data = _rewrite_rels(data, info.filename, replacement)
            entries.append((info, data))

    tmp = path.with_suffix(path.suffix + ".dedupe")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for info, data in entries:
            new_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            new_info.compress_type = info.compress_type
            new_info.external_attr = info.external_attr
            out.writestr(new_info, data)
    shutil.move(str(tmp), str(path))

    return DedupeResult(
        parts_before=len(media),
        parts_after=len(media) - len(replacement),
        bytes_before=original_size,
        bytes_after=path.stat().st_size,
    )


def _rewrite_rels(data: bytes, rels_name: str, replacement: dict[str, str]) -> bytes:
    """Point image relationships at the canonical copy of each image.

    ``rels_name`` looks like ``xl/drawings/_rels/drawing1.xml.rels``; targets in
    it are relative to ``xl/drawings``.
    """
    rels_dir = posixpath.dirname(rels_name)          # xl/drawings/_rels
    part_dir = posixpath.dirname(rels_dir)           # xl/drawings

    ET.register_namespace("", _RELS_NS)
    root = ET.fromstring(data)
    changed = False

    for rel in root:
        # Never touch a relationship that points outside the package.
        if rel.get("TargetMode") == "External":
            continue
        if rel.get("Type") != _IMAGE_TYPE:
            continue
        target = rel.get("Target")
        if not target:
            continue
        resolved = _resolve(target, part_dir)
        keeper = replacement.get(resolved)
        if keeper is not None:
            rel.set("Target", _relativise(keeper, part_dir))
            changed = True

    if not changed:
        return data
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
