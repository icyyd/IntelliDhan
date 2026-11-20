"""Read-only, bounded research download. Raw observations stay in ignored data/."""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import logging
import os
from pathlib import Path
import time
from urllib.request import urlopen

CBOE = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--cache", type=Path, default=Path("data/orb-study"))
    args = parser.parse_args()
    if not 0 < (args.end - args.start).days <= 1200:
        parser.error("request 1 to 1200 days")
    cache = args.cache.resolve()
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.umask(0o077)
    os.environ["HOME"] = str(cache)
    from moomoo import AuType, KLType, OpenQuoteContext, RET_OK, Session, SysConfig
    SysConfig.enable_console_log(False)
    logging.getLogger("FTFileLog").disabled = True
    ctx = OpenQuoteContext(host="127.0.0.1", port=11111, ai_type=1)
    try:
        ret, quota = ctx.get_history_kl_quota(get_detail=True)
        if ret != RET_OK or quota[1] < 2:
            raise RuntimeError("Unable to verify two available historical symbol allowances")
        print(f"Unused unique-symbol allowances: {quota[1]}; requesting at most two.", flush=True)
        for symbol in ("SPY", "QQQ"):
            dest = cache / f"{symbol}_{args.start}_{args.end}.csv"
            if dest.exists():
                print(f"Using existing {dest.name}")
                continue
            partial = dest.with_suffix(".partial")
            with partial.open("w", newline="") as f:
                writer = csv.writer(f)
                fields = ["time_key", "open", "high", "low", "close", "volume"]
                writer.writerow(fields)
                start = args.start
                rows = 0
                while start <= args.end:
                    end = min(args.end, start + timedelta(days=360))
                    cursor = None
                    while True:
                        ret, frame, cursor = ctx.request_history_kline(
                            f"US.{symbol}", start=start.isoformat(), end=end.isoformat(),
                            ktype=KLType.K_5M, autype=AuType.QFQ, session=Session.RTH,
                            max_count=1000, page_req_key=cursor)
                        if ret != RET_OK:
                            raise RuntimeError(f"Historical bars unavailable: {symbol}: {str(frame)[:220]}")
                        writer.writerows(frame[fields].itertuples(index=False, name=None))
                        rows += len(frame)
                        time.sleep(0.65)
                        if cursor is None:
                            break
                    print(f"{symbol} through {end}: {rows} bars", flush=True)
                    start = end + timedelta(days=1)
            if not rows:
                raise RuntimeError(f"No history for {symbol}")
            partial.replace(dest)
    finally:
        ctx.close()
    with urlopen(CBOE, timeout=20) as resp:
        data = resp.read(4_000_001)
    if len(data) > 4_000_000 or not data.startswith(b"DATE,OPEN,HIGH,LOW,CLOSE"):
        raise RuntimeError("Unrecognized Cboe VIX history")
    (cache / "VIX_History.csv").write_bytes(data)


if __name__ == "__main__":
    main()
