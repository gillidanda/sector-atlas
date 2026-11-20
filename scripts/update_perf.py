#!/usr/bin/env python3
"""Fetch end-of-day prices for every ticker in index.html and write perf.js.

perf.js defines one global:  const PERF = {asOf, updated, d: {TICKER: {p, w, m, q, h}}}
  p = last close, w/m/q/h = % return over 1 week / 1 month / 3 months / 6 months.

Run:  python scripts/update_perf.py
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
BATCH = 150
PERIODS = {  # key -> pandas DateOffset
    "w": pd.DateOffset(weeks=1),
    "m": pd.DateOffset(months=1),
    "q": pd.DateOffset(months=3),
    "h": pd.DateOffset(months=6),
}


def load_tickers():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    m = re.search(r"const DATA=(\[.*?\]);\s*const CAPN", html, re.S)
    data = json.loads(m.group(1))
    return sorted({t[0] for sec in data for sub in sec["s"] for t in sub["x"]})


def compute_returns(close: pd.Series):
    """close: daily closes indexed by date. Returns (last_date, last_price, {key: pct|None})."""
    close = close.dropna()
    if len(close) < 2:
        return None
    last_date, last = close.index[-1], float(close.iloc[-1])
    out = {}
    for key, off in PERIODS.items():
        target = last_date - off
        base = close[close.index <= target]
        # no price on/before the target date -> stock listed too recently for this window
        out[key] = round((last / float(base.iloc[-1]) - 1) * 100, 2) if len(base) else None
    return last_date, last, out


def fetch(symbols):
    import yfinance as yf

    df = yf.download(
        [s + ".NS" for s in symbols], period="9mo", interval="1d",
        auto_adjust=True, group_by="ticker", threads=True, progress=False,
    )
    res = {}
    for s in symbols:
        try:
            col = df[s + ".NS"]["Close"] if len(symbols) > 1 else df["Close"]
        except KeyError:
            continue
        col.index = pd.to_datetime(col.index).tz_localize(None).normalize()
        r = compute_returns(col)
        if r:
            res[s] = r
    return res


def main():
    tickers = load_tickers()
    got = {}
    for i in range(0, len(tickers), BATCH):
        got.update(fetch(tickers[i:i + BATCH]))
    missing = [t for t in tickers if t not in got]
    if missing:  # one retry for anything a batch dropped
        for i in range(0, len(missing), BATCH):
            got.update(fetch(missing[i:i + BATCH]))

    if len(got) < 0.7 * len(tickers):
        sys.exit(f"Only {len(got)}/{len(tickers)} tickers returned data; keeping old perf.js")

    # Market date = the most common latest date; drop stocks whose data stopped long ago (suspended/delisted)
    as_of = pd.Series([v[0] for v in got.values()]).mode().iloc[0]
    d = {}
    for t, (ld, price, rets) in got.items():
        if (as_of - ld).days > 5:
            continue
        d[t] = {"p": round(price, 2), **rets}

    payload = {
        "asOf": as_of.strftime("%Y-%m-%d"),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(d),
        "d": d,
    }
    (ROOT / "perf.js").write_text(
        "const PERF=" + json.dumps(payload, separators=(",", ":")) + ";\n", encoding="utf-8"
    )
    print(f"Wrote perf.js: {len(d)}/{len(tickers)} tickers, as of {payload['asOf']}")


if __name__ == "__main__":
    main()
