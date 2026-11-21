"""Tests for app.core, using small synthetic MRC files.

The pixel-for-pixel tests run the untouched original script
(tests/reference/mrc2png_original.py) on a copy of the same file and compare
the outputs. Set MRC2PNG_REAL_SAMPLE=/path/to/file.mrc to also check a real
(large) acquisition.
"""

import importlib.util
import os
import shutil
import threading
from pathlib import Path

import cv2
import mrcfile
import numpy as np
import pytest

from app import core
from app.core import Status

REF_PATH = Path(__file__).parent / "reference" / "mrc2png_original.py"


@pytest.fixture(scope="module")
def original():
    spec = importlib.util.spec_from_file_location("mrc2png_original", REF_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_mrc(path: Path, data: np.ndarray, voxel_a: float | None = 14.12) -> Path:
    with mrcfile.new(path, overwrite=True) as mrc:
        mrc.set_data(data)
        if voxel_a is not None:
            mrc.voxel_size = voxel_a
    return path


def rng_image(shape, dtype, seed=0):
    rng = np.random.default_rng(seed)
    if np.dtype(dtype).kind == "f":
        return (rng.normal(100, 30, shape)).astype(dtype)
    info = np.iinfo(dtype)
    lo, hi = max(info.min, 0 if info.min == 0 else -2000), min(info.max, 60000)
    # smooth-ish content plus noise, like a micrograph
    base = cv2.GaussianBlur(rng.uniform(lo, hi, shape).astype(np.float32), (0, 0), 3)
    return np.clip(base + rng.normal(0, 200, shape), lo, hi).astype(dtype)


def run_original(original, src: Path, tmp_path: Path, **kw) -> np.ndarray:
    d = tmp_path / "orig"
    d.mkdir(exist_ok=True)
    copy = d / src.name
    shutil.copy(src, copy)
    original.convert(copy, **kw)
    return cv2.imread(str(d / f"{src.stem}.png"), cv2.IMREAD_UNCHANGED)


# --------------------------------------------------------------------------
# Pixel-for-pixel against the original script
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "shape,dtype,voxel_a",
    [
        ((300, 500), "uint16", 14.12),      # typical JEM-1400 8000x, downsampled ~3.49x
        ((301, 497), "uint16", 14.120001),  # odd sizes, header float noise
        ((256, 256), "int16", 20.0),
        ((211, 333), "float32", 10.0),
        ((120, 90), "uint8", 30.0),
        ((200, 150), "uint16", 49.3),       # exactly the target: no resize
        ((200, 150), "uint16", 80.0),       # coarser: not upsampled
    ],
)
def test_matches_original(original, tmp_path, shape, dtype, voxel_a):
    src = make_mrc(tmp_path / "img.mrc", rng_image(shape, dtype), voxel_a)
    expected = run_original(original, src, tmp_path)
    res = core.convert_file(src, tmp_path / "out")
    got = cv2.imread(str(res.output), cv2.IMREAD_UNCHANGED)
    assert got.dtype == np.uint8 and got.ndim == 2
    assert got.shape == expected.shape
    assert np.array_equal(got, expected)


def test_fixed_factor_matches_original(original, tmp_path):
    src = make_mrc(tmp_path / "img.mrc", rng_image((300, 410), "uint16"), 14.12)
    expected = run_original(original, src, tmp_path, use_default_reduction_factor=True)
    res = core.convert_file(src, tmp_path / "out", fixed_factor=core.FIXED_REDUCTION_FACTOR)
    assert np.array_equal(cv2.imread(str(res.output), cv2.IMREAD_UNCHANGED), expected)


@pytest.mark.skipif(not os.environ.get("MRC2PNG_REAL_SAMPLE"), reason="MRC2PNG_REAL_SAMPLE not set")
def test_matches_original_real_sample(original, tmp_path):
    src = Path(os.environ["MRC2PNG_REAL_SAMPLE"])
    expected = run_original(original, src, tmp_path)
    res = core.convert_file(src, tmp_path / "out")
    assert np.array_equal(cv2.imread(str(res.output), cv2.IMREAD_UNCHANGED), expected)


# --------------------------------------------------------------------------
# Sizes, naming, statuses
# --------------------------------------------------------------------------
def test_output_size_and_name(tmp_path):
    src = make_mrc(tmp_path / "sample.mrc", rng_image((700, 1000), "uint16"), 14.12)
    res = core.convert_file(src, tmp_path / "out")
    reduction = 0.00493 / (np.float32(14.12) / 10000)
    assert res.output == tmp_path / "out" / "sample_resized.png"
    assert res.status == "converted"
    assert res.out_shape == (int(700 // reduction), int(1000 // reduction))
    assert res.out_px_size_nm == pytest.approx(4.93, rel=1e-6)


def test_coarser_is_flagged_not_upsampled(tmp_path):
    src = make_mrc(tmp_path / "c.mrc", rng_image((100, 120), "uint16"), 80.0)
    info = core.read_info(src)
    assert info.status == Status.COARSER and info.convertible
    res = core.convert_file(src, tmp_path / "out")
    assert res.status == "not_upsampled" and res.out_shape == (100, 120)
    assert "not upsampled" in res.message


def test_stack_rejected(tmp_path):
    src = make_mrc(tmp_path / "s.mrc", rng_image((3, 40, 50), "uint16"), 14.12)
    info = core.read_info(src)
    assert info.status == Status.STACK and not info.convertible
    assert "3 sections" in info.message
    with pytest.raises(core.StackNotSupported, match="3 sections"):
        core.convert_file(src, tmp_path / "out")
    assert not (tmp_path / "out" / "s_resized.png").exists()


def test_missing_pixel_size_flagged_and_override(tmp_path):
    src = make_mrc(tmp_path / "m.mrc", rng_image((200, 300), "uint16"), voxel_a=None)
    info = core.read_info(src)
    assert info.status == Status.MISSING_PX and info.px_size_nm is None
    with pytest.raises(core.MissingPixelSize):
        core.convert_file(src, tmp_path / "out")
    res = core.convert_file(src, tmp_path / "out", px_size_override_nm=1.412)
    # same result as if the header had said 14.12 A
    ref = make_mrc(tmp_path / "r.mrc", rng_image((200, 300), "uint16"), 14.12)
    ref_res = core.convert_file(ref, tmp_path / "out")
    assert np.array_equal(cv2.imread(str(res.output), -1), cv2.imread(str(ref_res.output), -1))


def test_unsupported_dtype(tmp_path):
    src = make_mrc(tmp_path / "f16.mrc", rng_image((60, 80), "float16"), 14.12)
    assert core.read_info(src).status == Status.UNSUPPORTED
    with pytest.raises(core.UnsupportedDataType):
        core.convert_file(src, tmp_path / "out")


def test_unreadable_file(tmp_path):
    bad = tmp_path / "bad.mrc"
    bad.write_bytes(b"not an mrc file")
    assert core.read_info(bad).status == Status.UNREADABLE
    res = core.convert_batch([bad], tmp_path / "out")
    assert res[0].status == "failed"


def test_skip_existing_and_overwrite(tmp_path):
    src = make_mrc(tmp_path / "e.mrc", rng_image((100, 100), "uint16"), 14.12)
    out = tmp_path / "out"
    assert core.convert_file(src, out).status == "converted"
    assert core.convert_file(src, out).status == "skipped"
    assert core.convert_file(src, out, overwrite=True).status == "converted"
    assert not list(out.glob("*.partial.png"))


def test_custom_target(tmp_path):
    src = make_mrc(tmp_path / "t.mrc", rng_image((400, 400), "uint16"), 10.0)  # 1 nm/px
    res = core.convert_file(src, tmp_path / "out", target_nm=4.0)
    assert res.out_shape == (100, 100)


# --------------------------------------------------------------------------
# Batch: discovery, cancel, duplicates, progress
# --------------------------------------------------------------------------
def test_find_mrc_files(tmp_path):
    (tmp_path / "sub").mkdir()
    for name in ["a.mrc", "sub/b.MRC", "sub/c.mrcs", "d.rec", "e.tif", "._a.mrc"]:
        (tmp_path / name).write_bytes(b"")
    names = sorted(p.name for p in core.find_mrc_files([tmp_path]))
    assert names == ["a.mrc", "b.MRC", "c.mrcs", "d.rec"]


def test_batch_cancel_between_files(tmp_path):
    files = [make_mrc(tmp_path / f"f{i}.mrc", rng_image((50, 60), "uint16", i), 14.12) for i in range(3)]
    cancel = threading.Event()
    events = []

    def cb(i, total, path, result):
        events.append((i, result is None))
        if result is not None and i == 0:
            cancel.set()

    res = core.convert_batch(files, tmp_path / "out", progress_cb=cb, cancel_event=cancel)
    assert [r.status for r in res] == ["converted", "cancelled", "cancelled"]
    assert events == [(0, True), (0, False)]
    assert len(list((tmp_path / "out").glob("*.png"))) == 1


def test_batch_duplicate_stems(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    a = make_mrc(tmp_path / "x" / "same.mrc", rng_image((50, 60), "uint16"), 14.12)
    b = make_mrc(tmp_path / "y" / "same.mrc", rng_image((50, 60), "uint16", 1), 14.12)
    res = core.convert_batch([a, b], tmp_path / "out")
    assert [r.status for r in res] == ["converted", "failed"]
    assert "same name" in res[1].message


def test_batch_continues_after_failure(tmp_path):
    stack = make_mrc(tmp_path / "s.mrc", rng_image((2, 40, 40), "uint16"), 14.12)
    ok = make_mrc(tmp_path / "ok.mrc", rng_image((40, 40), "uint16"), 14.12)
    res = core.convert_batch([stack, ok], tmp_path / "out")
    assert [r.status for r in res] == ["failed", "converted"]


def test_non_ascii_output_folder(tmp_path):
    # cv2.imwrite can't write to such paths on Windows; core writes the bytes itself.
    src = make_mrc(tmp_path / "img.mrc", rng_image((100, 120), "uint16"), 14.12)
    out = tmp_path / "Données été 📁"
    res = core.convert_file(src, out)
    assert res.output.parent == out and res.output.exists()
    decoded = cv2.imdecode(np.fromfile(res.output, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    ref = core.convert_file(src, tmp_path / "ascii")
    assert np.array_equal(decoded, cv2.imread(str(ref.output), cv2.IMREAD_UNCHANGED))
