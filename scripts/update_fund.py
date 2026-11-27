"""
Quarterly results for the hover card: Sales, EPS, net profit, operating profit, shares.

Source: Yahoo Finance (usually the last 5 quarters). Every quarter fetched is kept in
data/fund.json, so the history grows past 5 quarters over time. Writes fund.js for the page.

Gentle on Yahoo: stocks fetched longest ago go first, the run stops after a time budget
or if Yahoo starts refusing, and the next run carries on where this one stopped.

Run:  python scripts/update_fund.py
"""
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "data" / "fund.json"
OUT = ROOT / "fund.js"
KEEP_Q = 8            # quarters stored per stock
TIME_BUDGET = 12 * 60  # seconds
WORKERS = 6
STALE_DAYS = 1        # refetch a stock once per day at most

TYPES = {
    "quarterlyTotalRevenue": "s",
    "quarterlyNetIncome": "n",
    "quarterlyDilutedEPS": "e",
    "quarterlyOperatingIncome": "o",
}
SHARE_TYPES = ["quarterlyOrdinarySharesNumber", "annualOrdinarySharesNumber", "quarterlyBasicAverageShares"]
URL = ("https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{sym}"
       "?type={types}&period1=1500000000&period2={p2}")

try:  # yfinance installs curl_cffi, which looks like a normal browser to Yahoo
    from curl_cffi import requests as http
    GET = lambda u: http.get(u, impersonate="chrome", timeout=20)
except Exception:  # pragma: no cover
    import requests as http
    GET = lambda u: http.get(u, timeout=20, headers={"User-Agent": "Mozilla/5.0"})


class Refused(Exception):
    pass


def load_tickers():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    m = re.search(r"const DATA(?:_ALL)?=(\[.*?\]);\s*(?:let DATA=DATA_ALL;\s*)?const CAPN", html, re.S)
    data = json.loads(m.group(1))
    return sorted({t[0] for sec in data for sub in sec["s"] for t in sub["x"]})


def fetch_one(ticker):
    types = ",".join(list(TYPES) + SHARE_TYPES)
    url = URL.format(sym=ticker.replace("&", "%26") + ".NS", types=types, p2=int(time.time()) + 86400)
    r = GET(url)
    if r.status_code in (401, 403, 429):
        raise Refused(r.status_code)
    r.raise_for_status()
    res = (r.json().get("timeseries") or {}).get("result") or []
    quarters, shares = {}, {}
    for block in res:
        typ = (block.get("meta", {}).get("type") or [None])[0]
        for pt in block.get(typ) or []:
            if not pt or "reportedValue" not in pt:
                continue
            d, v = pt["asOfDate"], pt["reportedValue"].get("raw")
            if v is None:
                continue
            if typ in TYPES:
                quarters.setdefault(d, {})[TYPES[typ]] = v
            elif typ in SHARE_TYPES:
                shares.setdefault(typ, []).append((d, v))
    sh = None
    for typ in SHARE_TYPES:  # actual share count first, average shares as fallback
        if shares.get(typ):
            sh = sorted(shares[typ])[-1][1]
            break
    return quarters, sh


def main():
    tickers = set(load_tickers())
    hist = json.loads(HIST.read_text()) if HIST.exists() else {}
    today = date.today().isoformat()

    def age(t):
        f = hist.get(t, {}).get("f")
        return f or "0000-00-00"

    def stale(t):
        f = hist.get(t, {}).get("f")
        return not f or (date.today() - date.fromisoformat(f)).days >= STALE_DAYS

    todo = [t for t in sorted(tickers, key=age) if stale(t)]
    print(f"{len(todo)} stocks to refresh")

    start, done, refused, errors = time.time(), 0, 0, 0
    with ThreadPoolExecutor(WORKERS) as pool:
        it = iter(todo)
        running = {}
        def submit():
            t = next(it, None)
            if t is not None:
                running[pool.submit(fetch_one, t)] = t
        for _ in range(WORKERS):
            submit()
        while running:
            fut = next(as_completed(running))
            t = running.pop(fut)
            try:
                quarters, sh = fut.result()
                rec = hist.setdefault(t, {"q": {}})
                for d, vals in quarters.items():
                    rec["q"].setdefault(d, {}).update(vals)
                rec["q"] = dict(sorted(rec["q"].items())[-KEEP_Q:])
                if sh:
                    rec["sh"] = sh
                rec["f"] = today
                done += 1
                refused = 0
            except Refused:
                refused += 1
                time.sleep(5)
            except Exception as e:
                errors += 1
                print(f"{t}: {e}")
            if refused >= 20:
                print("Yahoo is refusing requests; stopping here, next run continues.")
                break
            if time.time() - start > TIME_BUDGET:
                print("Time budget used; next run continues.")
                break
            submit()

    HIST.parent.mkdir(exist_ok=True)
    HIST.write_text(json.dumps(hist, separators=(",", ":")))

    cr = lambda v: None if v is None else round(v / 1e7, 1)  # rupees -> crore
    d = {}
    for t, rec in hist.items():
        if t not in tickers:
            continue
        rows = []
        for qd, v in sorted(rec.get("q", {}).items(), reverse=True):  # newest first, up to KEEP_Q
            e = v.get("e")
            rows.append([qd, cr(v.get("s")), None if e is None else round(e, 2), cr(v.get("n")), cr(v.get("o"))])
        if rows:
            d[t] = {"sh": rec.get("sh"), "q": rows}
    OUT.write_text(
        "// Generated by scripts/update_fund.py. Quarterly results from Yahoo Finance, Rs crore.\n"
        "const FUND=" + json.dumps({"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "d": d}, separators=(",", ":")) + ";\n",
        encoding="utf-8")
    print(f"Refreshed {done} stocks ({errors} errors). fund.js has {len(d)} stocks.")


if __name__ == "__main__":
    main()
