"""Command-line interface (for power users and testing).

    python -m app convert -i <file-or-folder> [...] -o <output-folder>
    python -m app info -i <file-or-folder> [--csv header_data.csv]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from . import __version__
from .core import (
    DEFAULT_TARGET_NM,
    FIXED_REDUCTION_FACTOR,
    Status,
    convert_batch,
    find_mrc_files,
    read_info,
)


def _cmd_convert(args: argparse.Namespace) -> int:
    files = find_mrc_files(args.input)
    if not files:
        print("No .mrc/.mrcs/.rec files found.", file=sys.stderr)
        return 1
    overrides = {f: args.px_size_nm for f in files} if args.px_size_nm else None

    def progress(i, total, path, result):
        if result is None:
            print(f"[{i + 1}/{total}] {path.name} ...", flush=True)
        else:
            line = f"    {result.status}"
            if result.out_shape:
                line += f" -> {result.output.name} ({result.out_shape[1]}x{result.out_shape[0]})"
            if result.message:
                line += f": {result.message}"
            print(line, flush=True)

    results = convert_batch(
        files, args.output, target_nm=args.target_nm, px_size_overrides_nm=overrides,
        fixed_factor=FIXED_REDUCTION_FACTOR if args.fixed_factor else None,
        overwrite=args.overwrite, progress_cb=progress,
    )
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print("Done: " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
    return 1 if counts.get("failed") else 0


def _cmd_info(args: argparse.Namespace) -> int:
    files = find_mrc_files(args.input)
    rows = [("filename", "px_size (nm/px)", "width (px)", "height (px)", "sections", "status", "message")]
    unreadable = 0
    for f in files:
        info = read_info(f, args.target_nm)
        unreadable += info.status == Status.UNREADABLE
        px = f"{info.px_size_nm:.6g}" if info.px_size_nm else ""
        msg = "; ".join(filter(None, [info.message, *info.warnings]))
        rows.append((f.name, px, info.nx, info.ny, info.nz, info.status, msg))
        print(f"{f.name}\t{px or '?'} nm/px\t{info.nx}x{info.ny}x{info.nz}\t{info.status}")
    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            csv.writer(fh).writerows(rows)
        print(f"Wrote {args.csv}")
    return 1 if unreadable else 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mrc2png", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=f"mrc2png {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    c = sub.add_parser("convert", help="convert MRC files to resampled 8-bit PNGs")
    c.add_argument("-i", "--input", nargs="+", required=True, type=Path,
                   help=".mrc/.mrcs/.rec files or folders (searched recursively)")
    c.add_argument("-o", "--output", required=True, type=Path, help="output folder")
    c.add_argument("--target-nm", type=float, default=DEFAULT_TARGET_NM,
                   help=f"target pixel size in nm/px (default {DEFAULT_TARGET_NM})")
    c.add_argument("--px-size-nm", type=float, default=None,
                   help="override the header pixel size (nm/px) for all inputs")
    c.add_argument("--fixed-factor", action="store_true",
                   help=f"ignore pixel sizes and downsample by {FIXED_REDUCTION_FACTOR} "
                        "(legacy JEM-1400 8000x mode)")
    c.add_argument("--overwrite", action="store_true", help="overwrite existing PNGs")
    c.set_defaults(func=_cmd_convert)

    n = sub.add_parser("info", help="print header info (pixel size, size, sections)")
    n.add_argument("-i", "--input", nargs="+", required=True, type=Path)
    n.add_argument("--target-nm", type=float, default=DEFAULT_TARGET_NM)
    n.add_argument("--csv", type=Path, default=None, help="also write a CSV file")
    n.set_defaults(func=_cmd_info)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
