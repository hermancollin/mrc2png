"""Entry point: ``python -m app``. With arguments it runs the CLI.

Without arguments it will launch the GUI (step 2); until then it prints the
CLI help.
"""

import sys

from .cli import build_parser, main

if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(main())
    build_parser().print_help()
