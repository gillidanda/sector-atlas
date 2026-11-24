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
LIQUID_N = 1100  # keep the most-traded stocks, similar in size to 'actively traded' lists
PERIODS = {  # rolling windows: key -> pandas DateOffset
    "w": pd.DateOffset(weeks=1),   # rolling 1 week (used for strength/ratings)
    "m": pd.DateOffset(months=1),  # last 30-31 days
    "q": pd.DateOffset(months=3),  # last 90-91 days
    "h": pd.DateOffset(months=6),
}


def base_dates(last_date):
    """Calendar cut-offs. The base price is the last close ON OR BEFORE each date,
    so weekends and holidays are skipped automatically.
    tw = this week (so base = last close before Monday), tm = this month (last close of previous month)."""
    monday = last_date - pd.Timedelta(days=last_date.weekday())
    month_start = last_date.replace(day=1)
    d = {
        "tw": monday - pd.Timedelta(days=1),
        "tm": month_start - pd.Timedelta(days=1),
    }
    for k, off in PERIODS.items():
        d[k] = last_date - off
    return d


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
    for key, target in base_dates(last_date).items():
        base = close[close.index <= target]
        # no price on/before the target date -> stock listed too recently for this window
        out[key] = round((last / float(base.iloc[-1]) - 1) * 100, 2) if len(base) else None
    return last_date, last, out


def trend_info(close: pd.Series):
    """52-week-high distance and moving-average trend flags (EMA10/50/200 on daily closes)."""
    close = close.dropna()
    if len(close) < 60:
        return {}
    last = float(close.iloc[-1])
    hi = float(close[close.index > close.index[-1] - pd.DateOffset(years=1)].max())
    out = {"dh": round((last / hi - 1) * 100, 1)}  # % below the 52-week high (0 = at the high)
    e10 = float(close.ewm(span=10, adjust=False).mean().iloc[-1])
    e50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    out["a10"] = 1 if last > e10 else 0
    out["a50"] = 1 if last > e50 else 0
    if len(close) >= 200:
        e200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
        out["a200"] = 1 if last > e200 else 0
        out["u"] = 1 if (last > e50 and e50 > e200 and last > e200) else 0  # uptrend
    return out


def traded_value(close: pd.Series, vol: pd.Series):
    """Median daily traded value (Rs crore) over the last 20 sessions."""
    tv = (close * vol).dropna().tail(20)
    return round(float(tv.median()) / 1e7, 2) if len(tv) >= 10 else None


def fetch(symbols):
    import yfinance as yf

    df = yf.download(
        [s + ".NS" for s in symbols], period="14mo", interval="1d",
        auto_adjust=True, group_by="ticker", threads=True, progress=False,
    )
    res = {}
    for s in symbols:
        try:
            sub = df[s + ".NS"] if len(symbols) > 1 else df
            col, vol = sub["Close"], sub["Volume"]
        except KeyError:
            continue
        col.index = pd.to_datetime(col.index).tz_localize(None).normalize()
        vol.index = col.index
        r = compute_returns(col)
        if r:
            try:
                tv = traded_value(col, vol)
            except Exception:
                tv = None
            try:
                ex = trend_info(col)
            except Exception:
                ex = {}
            res[s] = r + (tv, ex)
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
    for t, (ld, price, rets, tv, ex) in got.items():
        if (as_of - ld).days > 5:
            continue
        d[t] = {"p": round(price, 2), **rets, "v": tv, **ex}
    ranked = sorted((t for t in d if d[t]["v"]), key=lambda t: -d[t]["v"])
    for i, t in enumerate(ranked):
        d[t]["l"] = 1 if i < LIQUID_N else 0
    for t in d:
        d[t].setdefault("l", 0)

    since = {k: v.strftime("%Y-%m-%d") for k, v in base_dates(as_of).items()}
    payload = {
        "asOf": as_of.strftime("%Y-%m-%d"),
        "since": since,
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
