"""depthfix CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

from . import __version__, engine, report
from .engine import Options

EXIT_OK = 0            # scan finished; nothing pending
EXIT_FINDINGS = 1      # findings remain (check mode) or could not all be fixed
EXIT_ERROR = 2         # operational error / bad arguments


def build_parser() -> argparse.ArgumentParser:
    epilog = """\
examples:
  depthfix                      scan the current directory (dry run report first time)
  depthfix --fix                rewrite manifests to supported versions
  depthfix --fix --no-install   rewrite manifests but skip package managers
  depthfix --check              CI mode: report only, exit 1 when findings exist
  depthfix --offline path/      use only the bundled deprecation registry
"""
    p = argparse.ArgumentParser(
        prog="depthfix",
        description="Deprecation registry scanner and fixer: scans project "
                    "dependency manifests for deprecated/EOL packages and "
                    "updates them to supported versions.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("paths", nargs="*", default=["."],
                   help="files or directories to scan (default: current directory)")
    p.add_argument("--fix", action="store_true", default=True,
                   help="rewrite manifests and run installers (default)")
    p.add_argument("--no-fix", dest="fix", action="store_false",
                   help="report only, never write files")
    p.add_argument("--dry-run", action="store_true",
                   help="show planned fixes without writing or installing")
    p.add_argument("--check", action="store_true",
                   help="report only and exit 1 if any finding remains (CI mode)")
    p.add_argument("--no-install", action="store_true",
                   help="rewrite manifests but skip package-manager commands")
    p.add_argument("--offline", action="store_true",
                   help="use only the bundled/user registry, no network calls")
    p.add_argument("--refresh", action="store_true",
                   help="ignore cached registry data and refetch")
    p.add_argument("--registry", action="append", default=[],
                   metavar="FILE",
                   help="extra deprecation registry JSON (repeatable; overrides bundled)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--version", action="version", version=f"depthfix {__version__}")
    return p


def main(argv: List[str] = None) -> int:
    args = build_parser().parse_args(argv)

    opts = Options(
        offline=args.offline,
        refresh=args.refresh,
        dry_run=args.dry_run or (not args.fix) or args.check,
        check=args.check,
        install=not args.no_install and not (args.dry_run or not args.fix or args.check),
        verbose=args.verbose,
        registries=args.registry,
    )

    try:
        roots: List[Path] = [Path(p).resolve() for p in args.paths]
        missing = [str(r) for r in roots if not r.exists()]
        if missing:
            print(f"depthfix: error: path(s) not found: {', '.join(missing)}",
                  file=sys.stderr)
            return EXIT_ERROR
        result = engine.run(roots, opts)
    except KeyboardInterrupt:
        print("depthfix: interrupted", file=sys.stderr)
        return EXIT_ERROR
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        if args.verbose:
            raise
        print(f"depthfix: error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    mode = ("check" if args.check
            else "dry-run" if args.dry_run or not args.fix
            else "auto-fix")

    if args.json:
        report.print_json(result, mode)
    else:
        report.print_report(result, verbose=args.verbose, note=mode)

    counts = result.counts()
    if args.check:
        return EXIT_FINDINGS if counts["deprecated"] or counts["outdated"] else EXIT_OK
    if counts["not_fixed"]:
        return EXIT_FINDINGS
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
