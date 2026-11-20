# mrc2png
Converts MRC files (electron microscopy) to 8-bit PNGs resampled to 4.93 nm/px, with min/max normalization and histogram equalization. It comes as a desktop app (GUI) and a command-line tool.

<p align="center">
  <img src="docs/screenshot.png" alt="The mrc2png window: a file list, an output folder picker, the target pixel size (4.93 nm/px) and Convert/Cancel buttons" width="720">
</p>

## Development setup

You need [uv](https://docs.astral.sh/uv/) and macOS on Apple Silicon. Use Python 3.12: its uv-managed build includes Tk, which the GUI needs. Homebrew's Python doesn't include Tk unless you also install `python-tk`.

```bash
uv venv --python 3.12 .venv/py312
uv pip install --python .venv/py312/bin/python -r requirements-dev.txt
```

Dependencies are pinned in `requirements.txt` (runtime) and `requirements-dev.txt` (tests and packaging). Keep `opencv-python-headless` pinned: the pixel-for-pixel tests compare against the original script, and a different OpenCV version could change the output.

### Run from source

Open the GUI:

```bash
.venv/py312/bin/python -m app
```

Convert from the command line. An output folder is required:

```bash
.venv/py312/bin/python -m app convert -i path/to/file_or_folder -o path/to/output
```

Print header info (pixel size, size, sections) and optionally save it as CSV:

```bash
.venv/py312/bin/python -m app info -i path/to/folder --csv header_data.csv
```

Other `convert` options: `--target-nm`, `--px-size-nm` (override the header pixel size), `--fixed-factor` (legacy 3.5× mode) and `--overwrite`. Run `python -m app convert --help` for details.

### Run the tests

```bash
.venv/py312/bin/python -m pytest -q
```

The tests compare the output pixel-for-pixel with the original script, kept unchanged in `tests/reference/mrc2png_original.py`. To also check against a real acquisition, point `MRC2PNG_REAL_SAMPLE` at an MRC file:

```bash
MRC2PNG_REAL_SAMPLE=/path/to/sample.mrc .venv/py312/bin/python -m pytest -q
```

## Building the macOS app

```bash
scripts/build_macos.sh .venv/py312/bin/python
```

This produces:

| File | What it is |
|---|---|
| `dist/mrc2png.app` | the app bundle; double-click to run |
| `dist/mrc2png-macos-arm64.zip` | the file to distribute (about 62 MB) |

The script deletes `build/` and `dist/`, runs PyInstaller with `mrc2png.spec`, then zips the app with `ditto`. Don't zip it with plain `zip`, which can break the bundle.

To test the build, double-click `dist/mrc2png.app`, or run the bundled program in CLI mode:

```bash
dist/mrc2png.app/Contents/MacOS/mrc2png convert -i path/to/file.mrc -o path/to/output
```

### Notes
- **Apple Silicon only.** PyInstaller can't cross-compile, so the app is built for the machine's architecture. An Intel build is a TODO.
- **macOS 14 or newer.** The NumPy 2.5 wheels require macOS 14, and the app's `LSMinimumSystemVersion` is set to match. When you bump dependencies, check the bundled binaries and update the value in `mrc2png.spec`:
  ```bash
  find dist/mrc2png.app -name "*.so" -o -name "*.dylib" | xargs -n1 vtool -show-build 2>/dev/null | awk '/minos/{print $2}' | sort -V | tail -1
  ```
- **Version.** The version number comes from `app/__init__.py` (`__version__`) and appears in the window title and `Info.plist`.
- **Icon.** The icon is `assets/icon.icns`, built from `assets/icon.png` (1024×1024). After changing the PNG, rebuild it:
  ```bash
  mkdir -p icon.iconset
  ```
  ```bash
  for s in 16 32 128 256 512; do sips -z $s $s assets/icon.png --out icon.iconset/icon_${s}x${s}.png; sips -z $((s*2)) $((s*2)) assets/icon.png --out icon.iconset/icon_${s}x${s}@2x.png; done
  ```
  ```bash
  iconutil -c icns icon.iconset -o assets/icon.icns && rm -r icon.iconset
  ```
- **Signing.** The app is only ad-hoc signed, so macOS warns on first launch. A TODO in `scripts/build_macos.sh` marks where Developer ID signing and notarization will go.

## Continuous integration and releases

The GitHub Actions workflow in `.github/workflows/build.yml` does the following:

| Trigger | What runs |
|---|---|
| Push or pull request to `gui` or `master` | tests |
| Manual run (Actions tab → *build* → *Run workflow*) | tests and app build; download the zip from the run's *Artifacts* |
| Version tag `v*` | tests, app build, and a GitHub Release with `mrc2png-macos-arm64.zip` attached |

The build job also smoke-tests the packaged app: it runs the bundled program in CLI mode on a small synthetic MRC file.

### Publishing a release

1. Set the new version in `app/__init__.py`, for example `__version__ = "0.2.0"`, and commit it.
2. Tag that commit with the same version, prefixed with `v`:
   ```bash
   git tag v0.2.0
   ```
3. Push the branch and the tag:
   ```bash
   git push origin HEAD v0.2.0
   ```

The workflow fails if the tag doesn't match `__version__`. Release notes are generated automatically from the commits and can be edited on GitHub afterwards.
