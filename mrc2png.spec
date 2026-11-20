# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the macOS app bundle.
#
#   pyinstaller --noconfirm mrc2png.spec      -> dist/mrc2png.app
#
# Build on the OS you are targeting (PyInstaller cannot cross-compile).
# Windows/Linux: TODO (deferred, see PLAN.md).
import re
from pathlib import Path

ROOT = Path(SPECPATH)
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "app" / "__init__.py").read_text()).group(1)
APP_NAME = "mrc2png"

# Big modules nothing here uses; keeps the bundle small.
EXCLUDES = [
    "matplotlib", "scipy", "PIL", "pandas", "IPython", "jupyter", "notebook",
    "pytest", "_pytest", "setuptools", "pkg_resources", "pip", "pydoc", "doctest",
    "unittest", "tkinter.test", "test", "lib2to3", "xmlrpc",
]

a = Analysis(
    [str(ROOT / "run_mrc2png.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,      # onedir: required for a well-behaved .app
    name=APP_NAME,
    debug=False,
    strip=False,
    upx=False,
    console=False,              # no terminal window
    argv_emulation=False,
    target_arch=None,           # native (arm64 on Apple Silicon). TODO: Intel build.
    codesign_identity=None,     # TODO: Developer ID identity for signing
    entitlements_file=None,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP_NAME)

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=str(ROOT / "assets" / "icon.icns"),
    bundle_identifier="io.github.hermancollin.mrc2png",
    version=VERSION,
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        # numpy 2.5 wheels need macOS 14, opencv 5.0 wheels macOS 13.
        # Check with `vtool -show-build` when bumping dependencies.
        "LSMinimumSystemVersion": "14.0",
        "NSHumanReadableCopyright": "Released on GitHub: hermancollin/mrc2png",
    },
)
