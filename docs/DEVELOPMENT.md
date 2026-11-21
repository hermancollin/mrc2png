# Development guide

How to set up a development environment, run the tests, build the macOS and Windows apps, and publish a release. Run all commands from the repository root.

## Development setup

You need [uv](https://docs.astral.sh/uv/). Use Python 3.12: its uv-managed build includes Tk, which the GUI needs. Homebrew's Python doesn't include Tk unless you also install `python-tk`.

On macOS:

```bash
uv venv --python 3.12 .venv/py312
uv pip install --python .venv/py312/bin/python -r requirements-dev.txt
```

On Windows (PowerShell):

```powershell
uv venv --python 3.12 .venv\py312
uv pip install --python .venv\py312\Scripts\python.exe -r requirements-dev.txt
```

The commands below use the macOS path `.venv/py312/bin/python`; on Windows, use `.venv\py312\Scripts\python.exe` instead.

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

## Building the Windows app

On a Windows machine, in PowerShell:

```powershell
scripts\build_windows.ps1
```

This uses `.venv\py312\Scripts\python.exe` by default; pass `-Python <path>` to use another interpreter. It produces `dist\mrc2png-windows-x64.exe`, a single file that contains the whole app. Double-click it to test.

If PowerShell refuses to run the script ("running scripts is disabled on this system"), allow local scripts for the current window only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### Notes
- **Single file.** On every launch the `.exe` unpacks itself to a temporary folder, so it takes a few seconds to open. OpenCV's video plugin (`opencv_videoio_ffmpeg*.dll`) is left out because the app never reads video.
- **Icon and version.** The icon is `assets/icon.ico`, generated from `assets/icon.png`; it's also bundled so the window uses it instead of Tk's default feather. After changing the PNG, regenerate it:
  ```bash
  uv run --no-project --with pillow python -c "from PIL import Image; Image.open('assets/icon.png').save('assets/icon.ico', sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])"
  ```
  The version shown in the `.exe`'s Properties → Details comes from `app/__init__.py`.
- **High-DPI screens.** The GUI declares itself DPI-aware and scales its pixel sizes, so it stays sharp at 125 % or 150 % display scaling.
- **Command line.** The `.exe` is a windowed app, but it still works from a terminal: `mrc2png-windows-x64.exe convert -i ... -o ...` writes its output to the terminal that started it. PowerShell and cmd may show the prompt again before the output ends.
- **Signing.** The `.exe` isn't signed, so SmartScreen warns on first launch, and some antivirus tools may be suspicious of it. A TODO in `scripts/build_windows.ps1` marks where `signtool` signing will go.

## Continuous integration and releases

The GitHub Actions workflow in `.github/workflows/build.yml` does the following:

| Trigger | What runs |
|---|---|
| Push or pull request to `gui` or `master` | tests on macOS and Windows |
| Manual run (Actions tab → *build* → *Run workflow*, pick a branch) | tests and both app builds; download them from the run's *Artifacts* |
| Version tag `v*` | tests, both app builds, and one GitHub Release with `mrc2png-macos-arm64.zip` and `mrc2png-windows-x64.exe` attached |

Each build job also smoke-tests the packaged app: it runs the bundled program in CLI mode on a small synthetic MRC file. The release is published by a separate job once both builds have succeeded, so a release never has only one of the two files.

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
