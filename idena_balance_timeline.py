#!/usr/bin/env python3
# coding: utf-8
# Build a wallet balance timeline for an Idena address.
# - Pages /Address/{addr}/Txs with continuationToken
# - Resolves each tx via /Transaction/{hash} to get blockHeight and timestamp
# - Classifies in vs out and reconstructs balance
# - Optionally calibrates to the current on-chain balance for an absolute curve
# - Saves JSONL, CSV, tail CSV, and two plots

import argparse, json, os, sys, time
from typing import Any, Dict, List, Optional, Tuple
from decimal import Decimal, InvalidOperation, getcontext
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
except Exception:
    plt = None

getcontext().prec = 28
BASE_URL = "https://api.idena.io/api"

# ---------- helpers ----------

def D(x) -> Decimal:
    try:
        if x is None:
            return Decimal(0)
        if isinstance(x, (int, float)):
            return Decimal(str(x))
        if isinstance(x, str):
            return Decimal(x.strip())
        return Decimal(0)
    except InvalidOperation:
        return Decimal(0)

def i(v, default=0) -> int:
    try:
        if v is None:
            return default
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("+"):
                s = s[1:]
            return int(s)
        return default
    except Exception:
        return default

def epoch(ts) -> int:
    if ts is None:
        return 0
    if isinstance(ts, int):
        return ts
    if isinstance(ts, float):
        return int(ts)
    if isinstance(ts, str):
        s = ts.strip()
        if s.isdigit():
            if len(s) >= 13:
                try:
                    return int(int(s) / 1000)
                except Exception:
                    return 0
            try:
                return int(s)
            except Exception:
                return 0
        try:
            if s.endswith("Z"):
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(s)
            return int(dt.timestamp())
        except Exception:
            try:
                dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
                return int(dt.replace(tzinfo=timezone.utc).timestamp())
            except Exception:
                return 0
    return 0

def iso_utc(ts_int: int) -> str:
    try:
        return datetime.utcfromtimestamp(int(ts_int)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""

def get_ci(dct: Dict[str, Any], *names):
    if not isinstance(dct, dict):
        return None
    if not names:
        return None
    keys = {k.lower(): k for k in dct.keys()}
    for n in names:
        k = keys.get(n.lower())
        if k is not None:
            return dct[k]
    return None

# ---------- API ----------

def extract_items_and_token(payload: Any) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    token = None
    items: List[Dict[str, Any]] = []
    if isinstance(payload, list):
        return payload, None
    if not isinstance(payload, dict):
        return [], None
    token = get_ci(payload, "continuationToken", "continuation_token")
    items = get_ci(payload, "items", "txs") or []
    if not items:
        res = get_ci(payload, "result")
        if isinstance(res, dict):
            token = get_ci(res, "continuationToken") or token
            items = get_ci(res, "items", "txs") or []
        elif isinstance(res, list):
            items = res
    if items and not isinstance(items[0], dict):
        items = [{"value": it} for it in items]
    return items, token

def get_current_balance(session: requests.Session, addr: str, base_url: str) -> Optional[Decimal]:
    url = f"{base_url}/Address/{addr}"
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        p = r.json()
        if isinstance(p, dict):
            cand = get_ci(p, "balance")
            if cand is None:
                res = get_ci(p, "result")
                if isinstance(res, dict):
                    cand = get_ci(res, "balance")
            if cand is None:
                dat = get_ci(p, "data")
                if isinstance(dat, dict):
                    cand = get_ci(dat, "balance")
            return D(cand) if cand is not None else None
        return None
    except Exception:
        return None

def fetch_all_txs(addr: str, limit: int, base_url: str, polite_sleep: float, max_pages: int=0, verbose: bool=False) -> List[Dict[str, Any]]:
    session = requests.Session()
    session.headers.update({"User-Agent": "idena-txs-fetcher/gh-1.0"})
    url = f"{base_url}/Address/{addr}/Txs"
    token = None
    page = 0
    out: List[Dict[str, Any]] = []
    while True:
        if max_pages and page >= max_pages:
            if verbose:
                print(f"[pager] reached max-pages={max_pages}, stopping")
            break
        params = {"limit": limit}
        if token:
            params["continuationToken"] = token
        for attempt in range(5):
            try:
                resp = session.get(url, params=params, timeout=30)
                if resp.status_code >= 500:
                    raise RuntimeError(f"server error {resp.status_code}")
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                wait = min(5.0, 0.5 * (attempt + 1) ** 2)
                print(f"[pager] request failed (attempt {attempt+1}/5): {e} - sleeping {wait}s")
                time.sleep(wait)
        else:
            print("[pager] giving up after 5 attempts")
            sys.exit(1)
        items, next_token = extract_items_and_token(data)
        got = len(items) if items else 0
        out.extend(items or [])
        page += 1
        token = next_token
        if verbose:
            print(f"[pager] page {page}: got {got} - total {len(out)} - token={'present' if token else 'none'}")
        if not token or got == 0:
            if verbose:
                print("[pager] no continuationToken or empty page - done")
            break
        time.sleep(polite_sleep)
    return out

def get_tx_hash_from_item(item: Dict[str, Any]) -> Optional[str]:
    v = get_ci(item, "hash", "txHash", "transactionHash", "id", "txId")
    if isinstance(v, str) and v.strip():
        return v.strip()
    txobj = get_ci(item, "tx", "transaction")
    if isinstance(txobj, dict):
        v2 = get_ci(txobj, "hash", "txHash", "transactionHash", "id", "txId")
        if isinstance(v2, str) and v2.strip():
            return v2.strip()
    return None

def fetch_tx_detail(session: requests.Session, base_url: str, h: str) -> Optional[Dict[str, Any]]:
    url = f"{base_url}/Transaction/{h}"
    for attempt in range(4):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 404:
                return None
            if r.status_code >= 500:
                raise RuntimeError(f"server error {r.status_code}")
            r.raise_for_status()
            p = r.json()
            if isinstance(p, dict):
                res = get_ci(p, "result")
                if isinstance(res, dict):
                    return res
                return p
            return None
        except Exception:
            time.sleep(0.3 * (attempt + 1))
    return None

def fetch_details_for_hashes(hashes: List[str], base_url: str, concurrency: int=8, cache_dir: str="tx_cache", force_refresh: bool=False, verbose: bool=False) -> Dict[str, Optional[Dict[str, Any]]]:
    os.makedirs(cache_dir, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "idena-tx-detail/gh-1.0"})
    results: Dict[str, Optional[Dict[str, Any]]] = {}
    def load_or_fetch(h: str):
        fp = os.path.join(cache_dir, f"{h}.json")
        if not force_refresh and os.path.exists(fp):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    return h, json.load(f)
            except Exception:
                pass
        detail = fetch_tx_detail(session, base_url, h)
        try:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(detail, f, ensure_ascii=False)
        except Exception:
            pass
        return h, detail
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futs = [ex.submit(load_or_fetch, h) for h in hashes]
        done = 0
        for fut in as_completed(futs):
            h, detail = fut.result()
            results[h] = detail
            done += 1
            if verbose and done % 200 == 0:
                print(f"[detail] fetched {done}/{len(hashes)}")
    return results

# ---------- transform ----------

def build_records(addr: str, items: List[Dict[str, Any]], details: Dict[str, Optional[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    my = addr.lower()
    recs: List[Dict[str, Any]] = []
    for it in items:
        h = get_tx_hash_from_item(it)
        if not h:
            continue
        det = details.get(h)
        if not isinstance(det, dict):
            continue
        block = get_ci(det, "blockHeight")
        ts = get_ci(det, "timestamp")
        typ = get_ci(det, "type")
        frm = get_ci(det, "from")
        to_ = get_ci(det, "to")
        amt = get_ci(det, "amount", "value")
        fee = get_ci(det, "fee")
        tips = get_ci(det, "tips")
        block_i = i(block, 0)
        ts_i = epoch(ts)
        if block_i <= 0:
            continue
        amt_d = D(amt)
        fee_d = D(fee)
        tips_d = D(tips)
        f_addr = (frm or "").lower()
        t_addr = (to_ or "").lower()
        if f_addr == my and t_addr != my:
            direction = "out"
            delta = -(amt_d + fee_d + tips_d)
        elif t_addr == my and f_addr != my:
            direction = "in"
            delta = amt_d
        elif t_addr == my and f_addr == my:
            direction = "self"
            delta = Decimal(0)
        else:
            direction = "other"
            delta = Decimal(0)
        recs.append({
            "hash": h,
            "block": block_i,
            "timestamp": ts_i,
            "direction": direction,
            "amount": str(amt_d),
            "fee": str(fee_d),
            "tips": str(tips_d),
            "type": typ or "",
            "delta": str(delta),
            "balance": None
        })
    return recs

def reconstruct_balance(addr: str, recs: List[Dict[str, Any]], base_url: str, calibrate: bool=True) -> Tuple[List[Dict[str, Any]], Optional[Decimal]]:
    session = requests.Session()
    session.headers.update({"User-Agent": "idena-balance/gh-1.0"})
    recs_desc = sorted(recs, key=lambda x: (x["block"], x["timestamp"]), reverse=True)
    recs_asc = list(reversed(recs_desc))
    curr_balance = None
    if calibrate:
        curr_balance = get_current_balance(session, addr, base_url)
    if curr_balance is not None:
        bal_before_oldest = curr_balance
        for r in recs_desc:
            if r["direction"] == "out":
                bal_before_oldest = bal_before_oldest + (D(r["amount"]) + D(r["fee"]) + D(r["tips"]))
            elif r["direction"] == "in":
                bal_before_oldest = bal_before_oldest - D(r["amount"])
        start_bal = bal_before_oldest
    else:
        start_bal = Decimal(0)
    bal = start_bal
    out: List[Dict[str, Any]] = []
    for r in recs_asc:
        if r["direction"] == "out":
            bal = bal - (D(r["amount"]) + D(r["fee"]) + D(r["tips"]))
        elif r["direction"] == "in":
            bal = bal + D(r["amount"])
        rr = dict(r)
        rr["balance"] = str(bal)
        out.append(rr)
    return out, curr_balance

# ---------- io ----------

def save_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

def save_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    import csv
    cols = ["hash","block","timestamp","direction","amount","fee","tips","type","delta","balance"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})

def save_tail_csv(rows: List[Dict[str, Any]], out_prefix: str, n: int) -> str:
    import csv
    tail = rows[-n:] if n > 0 else rows
    path = f"{out_prefix}.tail_{n}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["block","timestamp","iso_utc","direction","amount","fee","tips","balance","hash"])
        for r in tail:
            w.writerow([r["block"], r["timestamp"], iso_utc(r["timestamp"]), r["direction"], r["amount"], r["fee"], r["tips"], r["balance"], r["hash"]])
    return path

def plot_series(path_png: str, rows: List[Dict[str, Any]], title: str) -> None:
    if plt is None or not rows:
        return
    xs = [r["block"] for r in rows]
    ys = [float(D(r["balance"])) for r in rows]
    plt.figure()
    plt.plot(xs, ys, linewidth=1.5)
    ax = plt.gca()
    ax.grid(True, which="both", linestyle="--", linewidth=0.5, alpha=0.6)
    ax.xaxis.set_major_formatter(mticker.StrMethodFormatter("{x:,.0f}"))
    ax.yaxis.set_major_formatter(mticker.StrMethodFormatter("{x:,.3f}"))
    plt.xlabel("block")
    plt.ylabel("wallet balance [iDNA]")
    plt.title(title)
    plt.tight_layout()
    plt.savefig(path_png, dpi=180)
    plt.close()

# ---------- cli ----------

def main():
    ap = argparse.ArgumentParser(description="Build Idena wallet balance timeline with per-tx details")
    ap.add_argument("--address", required=True)
    ap.add_argument("--out-prefix", required=True)
    ap.add_argument("--title", default="wallet balance")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--sleep", type=float, default=0.25)
    ap.add_argument("--max-pages", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--cache-dir", default="tx_cache")
    ap.add_argument("--force-refresh", action="store_true")
    ap.add_argument("--no-calibrate", action="store_true", help="do not query current balance - relative curve from 0")
    ap.add_argument("--tail", type=int, default=25)
    ap.add_argument("--base-url", default=BASE_URL)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.cache_dir, exist_ok=True)

    print(f"Fetching tx list for {args.address} ...")
    items = fetch_all_txs(addr=args.address, limit=args.limit, base_url=args.base_url, polite_sleep=args.sleep, max_pages=args.max_pages, verbose=args.verbose)
    print(f"Fetched {len(items)} items - resolving details ...")
    hashes: List[str] = []
    for it in items:
        h = get_tx_hash_from_item(it)
        if h:
            hashes.append(h)
    hashes = list(dict.fromkeys(hashes))
    if args.verbose:
        print(f"Unique hashes: {len(hashes)}")
    details = fetch_details_for_hashes(hashes, base_url=args.base_url, concurrency=args.concurrency, cache_dir=args.cache_dir, force_refresh=args.force_refresh, verbose=args.verbose)
    recs = build_records(args.address, items, details)
    print(f"Records with blockHeight: {len(recs)}")
    if not recs:
        print("No usable records - exit")
        sys.exit(0)

    series, curr_balance = reconstruct_balance(args.address, recs, args.base_url, calibrate=not args.no_calibrate)
    if any(r["block"] == 0 for r in series):
        print("[fatal] a row with block=0 slipped through - abort")
        sys.exit(2)

    jsonl_path = f"{args.out_prefix}.timeline.jsonl"
    csv_path = f"{args.out_prefix}.timeline.csv"
    png_all = f"{args.out_prefix}.balance_all.png"
    png_last = f"{args.out_prefix}.balance_last_1000000.png"

    save_jsonl(jsonl_path, series)
    save_csv(csv_path, series)
    tail_path = save_tail_csv(series, args.out_prefix, n=args.tail)
    print(f"Wrote {jsonl_path}, {csv_path}, {tail_path}")

    plot_series(png_all, series, args.title)
    max_block = max(r["block"] for r in series)
    cutoff = max_block - 1_000_000
    last = [r for r in series if r["block"] >= cutoff]
    if last:
        plot_series(png_last, last, f"{args.title} - last 1,000,000 blocks")
        print(f"Saved plots: {png_all}, {png_last}")
    else:
        print(f"Saved plot: {png_all}")

    if curr_balance is not None:
        print(f"Current balance: {curr_balance} iDNA")
    else:
        print("Did not calibrate to current balance - curve starts at 0")

if __name__ == "__main__":
    main()
