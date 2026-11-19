"""6-K 등에서 찾은 실적 발표일 검증 — E 가 빠졌던 종목들."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nongaap as N  # noqa: E402

MISS = "SPOT FRO FMX XOM GMAB PHG GFL SAP CCJ NXE OKLO SPCX EMA DNN PUK DB AER".split()


def main():
    N.ALT_BUDGET = 900
    ciks = N._ciks()
    since = dt.date.today() - dt.timedelta(days=N.ANNUAL_LOOKBACK_DAYS)
    cache, todo, alts = {}, [], {}
    for t in MISS:
        rels, _, alt = N._releases(ciks[t], since)
        alts[t] = (rels, alt)
        todo += [(t, ciks[t], r) for r in alt if not r.get("src")]
    N._scan_alt(cache, todo)
    for t, (rels, alt) in alts.items():
        hits = [{**r, "score": cache[r["acc"]].get("score") or 1} for r in alt
                if (cache.get(r["acc"]) or {}).get("earn")]
        merged = N._pick_quarterly(sorted(rels + hits, key=lambda r: r["filed"]))
        merged = N._fill_periodic(merged, [r for r in alt if r.get("src")])
        print(f"{t:5s} 공시 {len(alt)}건 중 실적 {len(hits)}건 → E {len(merged)}개: "
              + " ".join(r["filed"] + ("*" if r.get("src") else "") for r in merged[-10:]))


if __name__ == "__main__":
    main()
