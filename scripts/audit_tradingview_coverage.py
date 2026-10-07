#!/usr/bin/env python3
"""Audit COV1 TradingView Trades CSV metadata against the frozen ORB calendar.

These exports are coverage markers, not raw data or a profitable strategy test.
Keep input CSVs and output in ignored data/research storage. Existing files are
never replaced, and this command does not create signals or execution intents.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from intellidhan_learning.tradingview_coverage import write_audit


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, action="append",
                        help="Trades CSV; repeat once for the other symbol")
    parser.add_argument("--out", type=Path, required=True, help="New private JSON result path")
    args = parser.parse_args(argv)
    try:
        report = write_audit(args.input, args.out)
    except (ValueError, OSError, UnicodeError, csv.Error) as exc:
        parser.exit(2, f"Coverage audit rejected: {exc}\n")
    for symbol, audit in report["instruments"].items():
        print(f"{symbol}: {len(audit['complete_rth_metadata_days'])}/"
              f"{audit['expected_sessions']} complete RTH metadata days; "
              f"{len(audit['missing_days'])} missing, {len(audit['invalid_days'])} invalid")
    print("Metadata only; no OHLCV, performance, ATR warmup or execution validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
