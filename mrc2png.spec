# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the desktop app.
#
#   pyinstaller --noconfirm mrc2png.spec
#     macOS   -> dist/mrc2png.app   (onedir .app bundle)
#     Windows -> dist/mrc2png.exe   (single file)
#
# Build on the OS you are targeting (PyInstaller cannot cross-compile).
# Use scripts/build_macos.sh or scripts/build_windows.ps1 rather than calling
# this directly: they also zip/rename the result for release.
import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH)
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "app" / "__init__.py").read_text()).group(1)
APP_NAME = "mrc2png"
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

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
    # Windows: Tk needs the .ico at runtime for the title bar / taskbar icon.
    datas=[(str(ROOT / "assets" / "icon.ico"), "assets")] if IS_WIN else [],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

if IS_WIN:
    # OpenCV's ffmpeg plugin (~25 MB) is only loaded for video I/O, which we
    # never use. (On macOS ffmpeg is linked into cv2 directly; keep it there.)
    a.binaries = [b for b in a.binaries if "opencv_videoio_ffmpeg" not in b[0].lower()]

pyz = PYZ(a.pure)

if IS_MAC:
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

elif IS_WIN:
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
    )

    nums = tuple(int(x) for x in re.findall(r"\d+", VERSION)[:3]) + (0,)
    nums = (nums + (0, 0, 0, 0))[:4]
    # Shown in the .exe's Properties -> Details tab.
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "mrc2png"),
                StringStruct("FileDescription", "mrc2png - MRC to PNG converter"),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", APP_NAME),
                StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
                StringStruct("ProductName", APP_NAME),
                StringStruct("ProductVersion", VERSION),
                StringStruct("Comments", "https://github.com/hermancollin/mrc2png"),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,                 # onefile: everything packed into the .exe
        a.datas,
        [],
        name=APP_NAME,
        debug=False,
        strip=False,
        upx=False,                  # UPX-packed exes trigger more antivirus false positives
        console=False,              # no terminal window
        icon=str(ROOT / "assets" / "icon.ico"),
        version=version_info,
        runtime_tmpdir=None,
        codesign_identity=None,     # Windows signing happens after the build (signtool), see build_windows.ps1
    )

else:
    raise SystemExit("Linux packaging is not configured yet (see PLAN.md).")
