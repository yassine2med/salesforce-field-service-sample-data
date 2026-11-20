"""Command line entry for the Field Service sample data checker and converter."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import check, write_tree


def main(argv: list[str] | None = None) -> int:
    """Return 0 when clean, 1 when issues exist, 2 when the folder is missing."""
    parser = argparse.ArgumentParser(
        prog="sfdata",
        description="Check Field Service sample CSVs and build sf data import tree files. No org is contacted.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    chk = sub.add_parser("check", help="validate the CSV files and the links between them")
    chk.add_argument("folder", nargs="?", default="data")
    build = sub.add_parser("build", help="check, then write JSON tree files and plan.json")
    build.add_argument("folder", nargs="?", default="data")
    build.add_argument("--out", default="tree", help="output folder (default tree)")
    args = parser.parse_args(argv)
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"Folder not found: {folder}", file=sys.stderr)
        return 2
    issues, counts = check(folder)
    for issue in issues:
        print(issue)
    summary = ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in counts.items())
    print(f"{summary}; {len(issues)} issue(s)")
    if args.command == "build":
        if issues:
            print("Not built: fix the issues above first", file=sys.stderr)
            return 1
        for name in write_tree(folder, Path(args.out)):
            print(f"wrote {Path(args.out) / name}")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
