"""Entry point: ``python -m app`` (and the packaged app).

With no arguments it opens the GUI; with arguments it runs the CLI.
"""

import os
import sys


def _ensure_std_streams(attach_console: bool) -> None:
    """Give a windowed (no-console) build somewhere to print.

    A PyInstaller build made with console=False on Windows has
    sys.stdout/sys.stderr set to None, so any print() would crash. For CLI
    use, attach to the console of the terminal that started us; otherwise
    discard the output.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    if attach_console and sys.platform == "win32":
        try:
            import ctypes

            if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
                con = open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace")
                sys.stdout = sys.stdout or con
                sys.stderr = sys.stderr or con
                return
        except Exception:  # noqa: BLE001
            pass
    devnull = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stdout or devnull
    sys.stderr = sys.stderr or devnull


def run() -> int:
    # macOS can pass a "-psn_..." process serial number to app bundles.
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]
    _ensure_std_streams(attach_console=bool(args))
    if args:
        from .cli import main

        return main(args)
    from .gui import main

    main()
    return 0


if __name__ == "__main__":
    sys.exit(run())
