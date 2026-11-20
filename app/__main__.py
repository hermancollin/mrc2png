"""Entry point: ``python -m app`` (and the packaged app).

With no arguments it opens the GUI; with arguments it runs the CLI.
"""

import sys


def run() -> int:
    # macOS can pass a "-psn_..." process serial number to app bundles.
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]
    if args:
        from .cli import main

        return main(args)
    from .gui import main

    main()
    return 0


if __name__ == "__main__":
    sys.exit(run())
