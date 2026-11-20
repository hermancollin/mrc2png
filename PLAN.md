# Plan: MRC → PNG Converter GUI (portable desktop app)

## Goal
Wrap the existing `mrc2png.py` script in a simple GUI. The script batch-converts MRC files (electron microscopy) to PNG and resamples them to **4.93 nm/px**. Package it as a portable, double-click app for collaborators. **macOS is the priority**; Windows and Linux come later. **End users must not need Python, a terminal, or any install step.**

All work happens on the `gui` branch. The repo is public on GitHub (`hermancollin/mrc2png`), so anyone can download the Release files.

## Context
- Users are EM researchers, not developers. Keep the UI minimal and the error messages plain.
- Sample data: `../raw_mrc_files/` holds 65 single-section uint16 MRCs, each about 13k × 30k px at 1.412 nm/px (about 800 MB). `../smaller_pngs/` holds earlier outputs named `<stem>_resized.png`.

## Behaviour of the existing script (`mrc2png.py`), to keep exactly
The refactored code must reproduce these steps in this order. Output must match the original pixel-for-pixel.
1. Read `mrc.data`. Read the pixel size from `mrc.voxel_size.x` (Å) and assume it is isotropic. Internally the script works in µm (Å / 10000); the UI shows nm (Å / 10). Both are the same scale.
2. Compute the resampling factor:
   - pixel size **finer** than the target: `reduction = target / px_size`
   - pixel size **coarser** than the target: `reduction = 1`, so the image is **not upsampled**
3. If `reduction != 1`, call `cv2.resize(img, (int(nx // reduction), int(ny // reduction)), interpolation=cv2.INTER_CUBIC)`. Keep the floor division, and keep the original dtype (uint16) during the resize.
4. Convert to `float32`, then `cv2.normalize(..., 0, 255, cv2.NORM_MINMAX)` (min/max over the resized image), then `uint8`.
5. Histogram-equalize with `cv2.equalizeHist`.
6. Write the result with `cv2.imwrite` as an 8-bit grayscale PNG.

The script also has a fixed-factor mode (`use_default_reduction_factor=True`, `REDUCTION_FACTOR = 3.5`). Note that 1.412 × 3.5 = 4.942, not 4.93. **The GUI always uses the header-based mode.** The fixed factor is exposed only as a CLI flag (`--fixed-factor`).

## Decisions (agreed with the user)
| Topic | Decision |
|---|---|
| Bit depth and contrast | Same as the script: 8-bit, min/max normalization, then histogram equalization. |
| Pixel size coarser than the target | Convert at the native scale (the script's behaviour), but **flag the row visibly** in the file table and in the summary ("not upsampled: X nm/px"). Never silently. |
| Multi-section stacks (nz > 1) | **Reject** the file with a clear message, e.g. "This file contains N sections; only single images are supported." Never crash. |
| Output naming | `<stem>_resized.png` |
| Output location | **The user must choose an output folder** before Convert is enabled. There is no default. Remember the last-used folder between sessions. |
| Existing output files | Skip a file whose output already exists, and log it as skipped. Offer an "Overwrite existing" checkbox, off by default. |
| Missing or zero pixel size | Flag the row. Let the user type a pixel size for it. Rows without a pixel size can't be converted. |
| Fixed reduction factor (3.5) | CLI-only flag; not in the GUI. |
| Target OS | macOS first (Apple Silicon). Windows and Linux are deferred (see the "Later" section). |

## Technical decisions
- **Image library:** OpenCV, as in the script. Use `opencv-python-headless` (no Qt, smaller bundle, packages cleanly with PyInstaller), pinned to the version in the current venv (5.0.0) so output stays identical. Pillow and scipy aren't needed.
- **GUI:** Tkinter (stdlib) with `ttk` widgets. Optional: `tkinterdnd2` for drag-and-drop, but only if it bundles cleanly into the macOS `.app`. If it doesn't, drop it.
- **Packaging:** PyInstaller `--windowed`, producing an `.app` bundle (onedir), zipped for download. PyInstaller 6 discourages onefile `.app` bundles.
- **Python:** 3.12 for building and CI. The current local venv is 3.14, which is too new for a reliable PyInstaller build. Pin dependencies in `requirements.txt`: `mrcfile`, `numpy`, `opencv-python-headless`. Put dev dependencies in `requirements-dev.txt`: `pytest`, `pyinstaller`.
- **Memory:** inputs are about 800 MB of uint16, and the resize plus the float32 conversion multiply that. Process **one file at a time** in a single worker thread. Open files with `mrcfile.mmap` or `mrcfile.open(..., permissive=False)` for the header scan, so that building the file table doesn't load the image data. Free arrays between files.

## Housekeeping (before step 1)
- ~~Move the uv venv out of `mrc2png/mrc2png/`~~ (done by the user: it's now at `.venv/mrc2png/`). Add `.venv/` to `.gitignore`.
- Move the original `mrc2png.py` to `tests/reference/mrc2png_original.py`, unchanged, as the oracle for the pixel-for-pixel test.
- Fold `read_mrc_head.py` into the CLI as an `info` subcommand that writes the same CSV columns, then remove the standalone script.
- Extend `.gitignore` to cover `build/`, `dist/`, `__pycache__/`, `*.egg-info/` and `.venv/`.

## Repo structure
The package directory is `app/`, not `mrc2png/`, so it doesn't share the repo's name. Run it with `python -m app`. The user-facing name of the app and the CLI is still "mrc2png".
```
app/
  __init__.py      # __version__
  core.py          # pure conversion logic (no GUI imports)
  gui.py           # Tkinter app
  cli.py           # CLI: convert (incl. --fixed-factor), info (header CSV)
  __main__.py      # launches the GUI with no args, the CLI with args
tests/
  reference/mrc2png_original.py
  test_core.py
.github/workflows/build.yml
mrc2png.spec       # PyInstaller spec
requirements.txt
requirements-dev.txt
README.md          # end-user instructions, including first-launch security warnings
```

## Steps

### 1. Refactor the core logic (`app/core.py`)
- `read_info(path) -> FileInfo`: reads only the header. Returns the pixel size in nm (or None if it is missing or zero), nx, ny, nz, dtype, and a status: `ok`, `coarser_than_target`, `missing_px_size` or `stack`.
- `convert_file(path, out_dir, target_nm_px=4.93, px_size_override_nm=None, fixed_factor=None, overwrite=False) -> ConvertResult`: carries out the steps above and returns the output path, the original and final pixel sizes, and any warnings.
- Raise clear, user-facing exceptions (`StackNotSupported`, `MissingPixelSize`, `UnsupportedDtype`) instead of crashing inside OpenCV. Note: `cv2.resize` doesn't accept every MRC dtype (e.g. int8, float16). Detect that and report it.
- `convert_batch(paths, ..., progress_cb, cancel_event)`: checks the cancel flag between files and reports per-file status.
- Tests:
  - Use small synthetic MRCs built with `mrcfile.new`: check the output size after resampling, uint8 dtype, `_resized.png` naming, a stack being rejected, a zero pixel size being flagged, a coarser-than-target file left unresampled and flagged, and cancel.
  - Pixel-for-pixel: on synthetic files, and on one real sample file if it's available locally (skipped otherwise), compare `core.convert_file` with `tests/reference/mrc2png_original.convert` using `np.array_equal`. The original writes `<stem>.png` next to the input, so run it on a temporary copy.

### 2. Build the GUI (`gui.py`)
- Input: "Add files…" and "Add folder…" buttons (recursive `*.mrc`, `*.mrcs`, `*.rec`), plus drag-and-drop if it's available.
- A file table with columns for name, size (px), pixel size (nm/px), status and result. Highlight flagged rows: missing pixel size (editable), coarser than target (warning), stack (rejected), unsupported dtype (rejected).
- An output folder picker, required. Convert stays disabled until a folder is chosen.
- A target pixel size field, pre-filled with 4.93 nm/px.
- An "Overwrite existing" checkbox.
- Convert and Cancel buttons, a progress bar (files done out of total, plus the current file's name) and a log panel.
- One worker thread. Send UI updates back through a `queue` polled with `root.after`. The UI must never freeze. Cancel takes effect after the current file finishes, and the app says so in the UI.
- When a run finishes, show a summary (converted, converted-but-not-upsampled, skipped, failed) and an "Open output folder" button (`open` on macOS).
- Show the app version in the window title.

### 3. Packaging (macOS)
- Write `mrc2png.spec`: a `--windowed` `.app` bundle with a bundle identifier and the version in `Info.plist`. Exclude unused heavy modules to keep the bundle small.
- Test locally on macOS. The `.app` must launch with no console window and convert a sample file.

**Status (done, pending the user's manual test):**
- `run_mrc2png.py` is the entry script, and `mrc2png.spec` builds a onedir `.app`. `scripts/build_macos.sh` builds `dist/mrc2png.app` and zips it with `ditto` as `dist/mrc2png-macos-arm64.zip`. CI will reuse this script.
- Icon: `assets/icon.png` (1024 px source) and `assets/icon.icns`.
- Bundle ID: `io.github.hermancollin.mrc2png`. Version from `app/__init__.py`. Ad-hoc signed by PyInstaller, arm64 only.
- **Minimum macOS 14 (Sonoma):** the numpy 2.5.4 wheel requires it (opencv 5.0 needs 13). Recheck with `vtool -show-build` when bumping dependencies.
- Size: about 155 MB unpacked, 62 MB zipped. Most of it is OpenCV's bundled ffmpeg and codec libraries, which `cv2` links against directly and so can't be trimmed safely.
- Build env: `.venv/py312` (uv-managed CPython 3.12.15, Tk 9.0) with PyInstaller 6.22.3 pinned in `requirements-dev.txt`.
- Drag-and-drop (`tkinterdnd2`) is not included yet. It isn't installed, and its compatibility with Tk 9 is untested. The GUI enables it automatically if it's present, so it can be evaluated later.

### 4. Continuous integration (macOS)
- `build.yml`: runs on `macos-latest` (arm64). Installs the dependencies, runs pytest, runs PyInstaller, then zips the `.app` as `mrc2png-macos-arm64.zip`. Triggered by version tags (`v*`). Uploads the artifact and attaches it to the GitHub Release.
- Also run pytest on pushes and PRs to `gui`/`master`, without packaging.
- Code signing and notarization are out of scope for now. Leave a clearly marked TODO step for macOS notarization.

**Status (written, not yet run on GitHub):** `.github/workflows/build.yml` has two jobs:
- `test`: runs on every push and PR to `gui`/`master`.
- `build-macos`: runs on `v*` tags and manual runs. It uses the uv-managed Python 3.12, reuses `scripts/build_macos.sh`, and smoke-tests the bundled app in CLI mode on a synthetic MRC. It uploads the zip as an artifact and, for tags, attaches it to a GitHub Release (`softprops/action-gh-release@v2`). It fails if the tag doesn't match `__version__`. There are TODO hooks for signing/notarization and for the Windows/Linux jobs. The release process is documented in the README.

### 5. End-user README
- The download link (latest Release) and first-launch steps on macOS. The app is unsigned, so Gatekeeper blocks it the first time. On macOS 15 and later, right-click → Open no longer works around this: users must open **System Settings → Privacy & Security** and click **"Open Anyway"**. Older macOS versions still allow right-click → Open. Document both.
- A 3-step usage guide (add files → choose output folder → Convert) with a screenshot placeholder.
- A short "what the conversion does" section: resampling to 4.93 nm/px, no upsampling, 8-bit with histogram equalization, `_resized.png` naming.
- A CLI section for power users.

**Status (done):** `README.md` is now a concise end-user page: screenshot, download and requirements, first-launch steps for macOS 15+ and 14, a 3-step guide, a status-column table and what the conversion does. Developer material (setup, run from source, CLI, tests, build, CI and releases) moved to `docs/DEVELOPMENT.md`, which the README links to. The download link points to `releases/latest`, which returns 404 until the first `v*` tag is published.

## Later (deferred)
- Windows (`--onefile --windowed`, `mrc2png-windows.exe`) and Linux (`mrc2png-linux`) builds: add them to the CI matrix. Document SmartScreen ("More info" → "Run anyway") and `chmod +x`.
- An Intel macOS build (a `macos-13` runner or a universal2 build), if any collaborator uses an Intel Mac.
- Code signing and notarization.
- Drag-and-drop with `tkinterdnd2`: check that it works with Tk 9 and bundles cleanly.
- Optional: a simplified icon for 16 and 32 px, where the "mrc2png" text is unreadable.

## Acceptance criteria
- [ ] A collaborator on macOS can download one zip, open the app, and convert a folder of MRCs without using a terminal.
- [ ] Output matches the original script pixel-for-pixel on reference files.
- [ ] The UI stays responsive during large batches, and Cancel works.
- [ ] Files with a missing or invalid pixel size are flagged and can't be converted until a pixel size is entered. Files coarser than the target are converted at the native scale and visibly flagged. Stacks are rejected with a clear message.
- [ ] A tagged push produces the macOS release artifact.

## Resolved
- Target Apple Silicon only. An Intel macOS build stays a TODO (see "Later").
- Raw images can reach several GB. Strictly one file at a time: no parallel workers, and no preloading of the next file.
- The uv venv now lives at `.venv/mrc2png/` (moved by the user).
