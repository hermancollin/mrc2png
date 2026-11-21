"""Pure conversion logic: MRC -> 8-bit PNG resampled to a target pixel size.

No GUI imports here. The conversion reproduces the original ``mrc2png.py``
script exactly (kept in ``tests/reference/mrc2png_original.py``):

1. read ``mrc.data`` and ``mrc.voxel_size.x`` (Angstrom, assumed isotropic);
2. if the pixel size is finer than the target, downsample by
   ``target / px_size`` with ``cv2.INTER_CUBIC``; if it is coarser, keep the
   native size (never upsample);
3. min/max normalize to 0-255 in float32, cast to uint8;
4. ``cv2.equalizeHist``;
5. write ``<stem>_resized.png``.

Internally the arithmetic is done in um/px with the same numpy types as the
original script so the output matches pixel-for-pixel. User-facing values
are in nm/px.
"""

from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import cv2
import mrcfile
import numpy as np

# Same literal as the original script (um/px). Keep it a literal: deriving it
# from 4.93 / 1000 would give a slightly different float.
DEFAULT_TARGET_UM = 0.00493
DEFAULT_TARGET_NM = 4.93
# Fixed factor of the original script: JEM-1400 SerialEM 8000x (1.412 nm/px)
# to ~4.93 nm/px. CLI-only.
FIXED_REDUCTION_FACTOR = 3.5

MRC_EXTENSIONS = (".mrc", ".mrcs", ".rec")
OUTPUT_SUFFIX = "_resized.png"

# dtypes cv2.resize(INTER_CUBIC) accepts (checked with OpenCV 5.0.0).
_RESIZABLE_DTYPES = {np.dtype(t) for t in ("uint8", "int16", "uint16", "float32", "float64")}
_ANISOTROPY_TOL = 1e-3


# --------------------------------------------------------------------------
# Errors: messages are shown as-is to end users, keep them plain.
# --------------------------------------------------------------------------
class ConversionError(Exception):
    """Base class for problems that are reported to the user, not crashes."""


class StackNotSupported(ConversionError):
    pass


class MissingPixelSize(ConversionError):
    pass


class UnsupportedDataType(ConversionError):
    pass


class UnreadableFile(ConversionError):
    pass


# --------------------------------------------------------------------------
# Header inspection
# --------------------------------------------------------------------------
class Status:
    OK = "ok"
    COARSER = "coarser_than_target"      # converted at native scale, flagged
    MISSING_PX = "missing_px_size"       # needs a user-entered pixel size
    STACK = "stack"                      # rejected
    UNSUPPORTED = "unsupported_dtype"    # rejected
    UNREADABLE = "unreadable"            # rejected


@dataclass
class FileInfo:
    path: Path
    nx: int = 0                 # width (px)
    ny: int = 0                 # height (px)
    nz: int = 0                 # number of sections
    dtype: str = ""
    px_size_nm: float | None = None   # None when missing/zero in the header
    status: str = Status.OK
    message: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def convertible(self) -> bool:
        return self.status in (Status.OK, Status.COARSER)


def px_size_nm_from_voxel(voxel_x) -> float | None:
    """Header voxel size (Angstrom) -> nm/px, or None if missing/invalid."""
    v = float(voxel_x)
    if not math.isfinite(v) or v <= 0:
        return None
    return v / 10


def nm_to_um(nm: float) -> float:
    """nm -> um, rounded so 4.93 maps to exactly the 0.00493 literal."""
    return round(float(nm) / 1000, 12)


def output_path_for(path: Path, out_dir: Path) -> Path:
    return Path(out_dir) / f"{Path(path).stem}{OUTPUT_SUFFIX}"


def _needs_resize(px_size_nm: float, target_nm: float) -> bool:
    return px_size_nm < target_nm


def read_info(path: Path, target_nm: float = DEFAULT_TARGET_NM) -> FileInfo:
    """Read only the header (no image data) and classify the file."""
    path = Path(path)
    info = FileInfo(path=path)
    try:
        with mrcfile.open(path, header_only=True, permissive=True) as mrc:
            h = mrc.header
            info.nx, info.ny, info.nz = int(h.nx), int(h.ny), int(h.nz)
            vs = mrc.voxel_size
            try:
                info.dtype = str(mrcfile.utils.dtype_from_mode(h.mode))
            except ValueError:
                info.dtype = f"mode {int(h.mode)}"
    except Exception as e:  # noqa: BLE001 - any read problem is reported
        info.status = Status.UNREADABLE
        info.message = f"Could not read this file as MRC ({e})."
        return info

    info.px_size_nm = px_size_nm_from_voxel(vs.x)
    vx, vy = float(vs.x), float(vs.y)
    if info.px_size_nm and vy > 0 and abs(vx - vy) > _ANISOTROPY_TOL * vx:
        info.warnings.append(
            f"Pixel size differs in X ({vx / 10:.4g} nm) and Y ({vy / 10:.4g} nm); X is used."
        )

    if info.nz > 1:
        info.status = Status.STACK
        info.message = (
            f"This file contains {info.nz} sections; only single images are supported."
        )
    elif info.px_size_nm is None:
        info.status = Status.MISSING_PX
        info.message = "Pixel size missing from the file header. Enter it manually."
    else:
        info.status, info.message = classify_scale(info.px_size_nm, target_nm, info.dtype)
    return info


def classify_scale(px_size_nm: float, target_nm: float, dtype: str = "") -> tuple[str, str]:
    """Status for a file with a known pixel size (header or user-entered)."""
    if _needs_resize(px_size_nm, target_nm):
        if dtype and not dtype.startswith("mode") and np.dtype(dtype) not in _RESIZABLE_DTYPES:
            return Status.UNSUPPORTED, f"Image data type '{dtype}' cannot be resampled."
        return Status.OK, ""
    if px_size_nm > target_nm:
        return Status.COARSER, (
            f"Pixel size {px_size_nm:.4g} nm is larger than the target "
            f"{target_nm:.4g} nm; converted at native scale (not upsampled)."
        )
    return Status.OK, ""


def find_mrc_files(paths: Iterable[Path]) -> list[Path]:
    """Expand files and folders (recursive) into a sorted, de-duplicated list."""
    found: dict[Path, None] = {}
    for p in map(Path, paths):
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in MRC_EXTENSIONS and not f.name.startswith("._"):
                    found[f.resolve()] = None
        elif p.is_file():
            found[p.resolve()] = None
    return list(found)


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------
@dataclass
class ConvertResult:
    path: Path
    output: Path | None
    status: str                 # "converted", "not_upsampled", "skipped", "failed", "cancelled"
    message: str = ""
    px_size_nm: float | None = None
    out_px_size_nm: float | None = None
    out_shape: tuple[int, int] | None = None   # (height, width)


def convert_file(
    path: Path,
    out_dir: Path,
    target_nm: float = DEFAULT_TARGET_NM,
    px_size_override_nm: float | None = None,
    fixed_factor: float | None = None,
    overwrite: bool = False,
) -> ConvertResult:
    """Convert one single-section MRC file. Raises ConversionError on user-level problems.

    ``px_size_override_nm`` replaces the header value (for files whose header
    has no pixel size). ``fixed_factor`` reproduces the original script's
    ``use_default_reduction_factor=True`` mode (pixel size ignored).
    """
    path = Path(path)
    out_dir = Path(out_dir)
    output = output_path_for(path, out_dir)
    if output.exists() and not overwrite:
        return ConvertResult(path, output, "skipped", "Output already exists.")

    target_um = DEFAULT_TARGET_UM if target_nm == DEFAULT_TARGET_NM else nm_to_um(target_nm)

    try:
        mrc_ctx = mrcfile.open(path, permissive=True)
    except Exception as e:  # noqa: BLE001
        raise UnreadableFile(f"Could not read this file as MRC ({e}).") from e

    with mrc_ctx as mrc:
        if mrc.data is None:
            raise UnreadableFile("The file contains no image data.")
        img = mrc.data
        # Kept as numpy scalars on purpose: same arithmetic as the original.
        nx = mrc.header.nx
        ny = mrc.header.ny
        if img.ndim != 2:
            n = img.shape[0] if img.ndim == 3 else "several"
            raise StackNotSupported(
                f"This file contains {n} sections; only single images are supported."
            )
        if px_size_override_nm is not None:
            px_size = np.float32(float(px_size_override_nm) * 10) / 10000
        else:
            px_size = mrc.voxel_size.x / 10000  # Angstrom to um (float32, as original)

    if np.iscomplexobj(img):
        raise UnsupportedDataType(f"Image data type '{img.dtype}' is not supported.")

    status = "converted"
    message = ""
    if fixed_factor is not None:
        reduction = fixed_factor
    else:
        if not (math.isfinite(float(px_size)) and float(px_size) > 0):
            raise MissingPixelSize("Pixel size missing from the file header. Enter it manually.")
        if px_size > target_um:
            reduction = 1
            status = "not_upsampled"
            message = (
                f"Pixel size {float(px_size) * 1000:.4g} nm is larger than the target; "
                "converted at native scale (not upsampled)."
            )
        else:
            reduction = target_um / px_size

    # resize
    if reduction != 1:
        if img.dtype.newbyteorder("=") not in _RESIZABLE_DTYPES:
            raise UnsupportedDataType(f"Image data type '{img.dtype}' cannot be resampled.")
        if not img.dtype.isnative:
            img = img.astype(img.dtype.newbyteorder("="))
        reduced_size = (int(nx // reduction), int(ny // reduction))
        if reduced_size[0] < 1 or reduced_size[1] < 1:
            raise ConversionError("Image is too small to be resampled to the target pixel size.")
        img = cv2.resize(img, reduced_size, interpolation=cv2.INTER_CUBIC)
    # normalization
    img = img.astype(np.float32)
    img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)
    img = img.astype(np.uint8)
    # histogram equalization
    equalized = cv2.equalizeHist(img)
    del img

    # Encode in memory and write with Python: cv2.imwrite can't handle
    # non-ASCII paths on Windows (e.g. a folder named "Données").
    ok, png = cv2.imencode(".png", equalized)
    if not ok:
        raise ConversionError("Could not encode the PNG.")
    tmp = output.with_name(output.stem + ".partial.png")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(png.tobytes())
        os.replace(tmp, output)
    except OSError as e:
        raise ConversionError(f"Could not write the PNG to {out_dir} ({e.strerror or e}).") from e
    finally:
        if tmp.exists():
            tmp.unlink()

    px_nm = float(px_size) * 1000 if fixed_factor is None or px_size > 0 else None
    return ConvertResult(
        path=path,
        output=output,
        status=status,
        message=message,
        px_size_nm=px_nm,
        out_px_size_nm=px_nm * float(reduction) if px_nm else None,
        out_shape=tuple(int(s) for s in equalized.shape),
    )


ProgressCallback = Callable[[int, int, Path, "ConvertResult | None"], None]


def convert_batch(
    paths: Iterable[Path],
    out_dir: Path,
    target_nm: float = DEFAULT_TARGET_NM,
    px_size_overrides_nm: dict[Path, float] | None = None,
    fixed_factor: float | None = None,
    overwrite: bool = False,
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> list[ConvertResult]:
    """Convert files strictly one at a time (inputs can be several GB each).

    ``progress_cb(index, total, path, result)`` is called with ``result=None``
    when a file starts and with its result when it ends. Cancellation is
    checked between files; the file in progress always finishes.
    """
    paths = [Path(p) for p in paths]
    overrides = px_size_overrides_nm or {}
    total = len(paths)
    results: list[ConvertResult] = []

    # Two inputs with the same stem would write the same output file.
    seen: dict[Path, Path] = {}
    duplicates: dict[Path, Path] = {}
    for p in paths:
        out = output_path_for(p, out_dir)
        if out in seen:
            duplicates[p] = seen[out]
        else:
            seen[out] = p

    for i, p in enumerate(paths):
        if cancel_event is not None and cancel_event.is_set():
            results.extend(ConvertResult(q, None, "cancelled", "Cancelled.") for q in paths[i:])
            break
        if progress_cb:
            progress_cb(i, total, p, None)
        if p in duplicates:
            res = ConvertResult(
                p, None, "failed",
                f"Another file in this batch has the same name ({duplicates[p]}); "
                "both would be written to the same PNG.",
            )
        else:
            try:
                res = convert_file(
                    p, out_dir, target_nm=target_nm,
                    px_size_override_nm=overrides.get(p),
                    fixed_factor=fixed_factor, overwrite=overwrite,
                )
            except ConversionError as e:
                res = ConvertResult(p, None, "failed", str(e))
            except MemoryError:
                res = ConvertResult(p, None, "failed", "Not enough memory to convert this file.")
            except Exception as e:  # noqa: BLE001 - one bad file must not stop the batch
                res = ConvertResult(p, None, "failed", f"Unexpected error: {e}")
        results.append(res)
        if progress_cb:
            progress_cb(i, total, p, res)
    return results
