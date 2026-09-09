"""Prepare photos for embedding: honour EXIF rotation, optionally downscale.

Two things matter here that are easy to get wrong.

First, phone cameras record orientation in an EXIF tag rather than rotating the
pixels.  Image viewers honour that tag; Excel does not.  A portrait tilt photo
embedded verbatim therefore shows up on its side.  We bake the rotation into the
pixels before embedding.

Second, the source photos are full-resolution camera files.  A site with 130 of
them produces a workbook too large to email.  Photos larger than ``max_dim`` on
their long edge are scaled down, which keeps a tilt reading perfectly legible
while cutting the file size by roughly an order of magnitude.

A photo that needs neither treatment is embedded byte-for-byte from the original
file, so the common case is lossless.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

# Cameras in this survey top out around 4000 px; 2400 keeps detail while
# roughly halving the linear dimensions of the largest files.
DEFAULT_MAX_DIM = 2400
JPEG_QUALITY = 88

# Suffixes we can hand to Excel untouched.
EXCEL_SAFE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp"}
# Colour modes Excel renders reliably.  A CMYK JPEG (common after a round trip
# through Adobe tooling) or a 16-bit PNG is re-encoded rather than passed
# through, because Excel would otherwise show a blank frame or refuse the part.
EXCEL_SAFE_MODES = {"RGB", "RGBA", "L", "LA", "P", "1"}


@dataclass(frozen=True)
class PreparedImage:
    """A photo ready to embed, with the pixel size Excel should draw it at."""

    path: Path        # file to embed (the original, or a processed copy)
    width: int
    height: int
    source: Path
    reused_original: bool
    digest: str       # hash of the embedded bytes, used to de-duplicate media


class ImagePreparer:
    """Prepares photos into ``work_dir``, caching by source path.

    The same photo can legitimately appear in more than one slot (the 850 and
    900 tilt shots are frequently the same file), so results are memoised.
    """

    def __init__(
        self,
        work_dir: Path,
        max_dim: int | None = DEFAULT_MAX_DIM,
    ) -> None:
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.max_dim = max_dim
        self._cache: dict[Path, PreparedImage | None] = {}
        self.failures: list[tuple[Path, str]] = []

    def prepare(self, source: Path) -> PreparedImage | None:
        """Return an embeddable version of ``source``, or ``None`` if unreadable."""
        if source in self._cache:
            return self._cache[source]
        try:
            result = self._prepare_uncached(source)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop a run
            self.failures.append((source, f"{type(exc).__name__}: {exc}"))
            result = None
        self._cache[source] = result
        return result

    def _prepare_uncached(self, source: Path) -> PreparedImage:
        with Image.open(source) as img:
            img.load()
            oriented = ImageOps.exif_transpose(img)
            rotated = oriented.size != img.size or oriented is not img
            width, height = oriented.size

            longest = max(width, height)
            needs_resize = self.max_dim is not None and longest > self.max_dim
            suffix = source.suffix.lower()
            needs_reencode = suffix not in EXCEL_SAFE_SUFFIXES

            safe_mode = oriented.mode in EXCEL_SAFE_MODES

            # Nothing to change: embed the original bytes, losslessly.
            if (
                not needs_resize
                and not needs_reencode
                and safe_mode
                and not _has_exif_rotation(img)
            ):
                data = source.read_bytes()
                return PreparedImage(
                    path=source,
                    width=width,
                    height=height,
                    source=source,
                    reused_original=True,
                    digest=hashlib.sha256(data).hexdigest(),
                )

            processed = oriented
            if needs_resize:
                scale = self.max_dim / longest
                width = max(1, round(width * scale))
                height = max(1, round(height * scale))
                processed = oriented.resize((width, height), Image.LANCZOS)

            out_path, data = self._encode(processed, source, rotated or needs_resize)
            return PreparedImage(
                path=out_path,
                width=width,
                height=height,
                source=source,
                reused_original=False,
                digest=hashlib.sha256(data).hexdigest(),
            )

    def _encode(
        self, image: Image.Image, source: Path, _changed: bool
    ) -> tuple[Path, bytes]:
        """Write ``image`` into the work directory and return its path and bytes."""
        keep_png = source.suffix.lower() == ".png" and image.mode in ("RGBA", "P", "LA")
        stem = hashlib.sha1(str(source).encode()).hexdigest()[:16]

        if keep_png:
            out_path = self.work_dir / f"{stem}.png"
            image.save(out_path, format="PNG", optimize=True)
        else:
            out_path = self.work_dir / f"{stem}.jpg"
            if image.mode != "RGB":
                image = image.convert("RGB")
            image.save(
                out_path, format="JPEG", quality=JPEG_QUALITY, optimize=True,
                progressive=True,
            )
        return out_path, out_path.read_bytes()


def _has_exif_rotation(img: Image.Image) -> bool:
    """True when the file carries an EXIF orientation Excel would ignore."""
    try:
        exif = img.getexif()
    except Exception:  # noqa: BLE001 - malformed EXIF is not fatal
        return False
    return exif.get(0x0112, 1) not in (1, None)
