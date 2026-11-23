#!/usr/bin/env python3
"""Seven-role PAPER-ONLY crypto research desk.

Commands:
  check-data   verify connectivity, symbols and data freshness
  scan         run all seven roles; may save a PENDING proposal (never a fill)
  paper-step   fill/advance paper positions from LATER observed prices only
  report       equity, P&L, drawdown, counts and equal-weight hold comparison
  smoke-test   deterministic end-to-end run on SYNTHETIC data (output/SYNTHETIC_smoke/)

No wallets, API keys, order endpoints or live execution exist in this project.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from deskcore.models import DataError
from deskcore.pipeline import Context, check_data, paper_step, report, scan
from deskcore.sources import LiveSource
from deskcore.synthetic import run_smoke

ROOT = Path(__file__).resolve().parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check-data", "scan", "paper-step", "report", "smoke-test"])
    ap.add_argument("--root", type=Path, default=ROOT, help="project folder (default: this folder)")
    args = ap.parse_args(argv)

    if args.command == "smoke-test":
        res = run_smoke(args.root / "output" / "SYNTHETIC_smoke")
        print(f"\nSYNTHETIC smoke-test files written to {res['root']}")
        return 0

    ctx = Context(root=args.root, source=LiveSource(snapshot_dir=args.root / "data" / "snapshots"))
    try:
        if args.command == "check-data":
            return 0 if check_data(ctx) else 1
        if args.command == "scan":
            scan(ctx)
        elif args.command == "paper-step":
            paper_step(ctx)
        elif args.command == "report":
            report(ctx)
    except DataError as exc:
        print(f"\nSTOPPED (fail-closed): {exc}\nExisting records were left unchanged.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
