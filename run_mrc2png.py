"""PyInstaller entry script (the packaged app starts here).

PyInstaller needs a plain script; ``app/__main__.py`` uses relative imports,
so it can't be used directly.
"""

import sys

from app.__main__ import run

if __name__ == "__main__":
    sys.exit(run())
