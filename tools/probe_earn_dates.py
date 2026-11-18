"""차트 E(실적 발표일) 표시가 빠지는 종목 전수 조사.

E 는 SEC 실적 8-K(Item 2.02) 접수일로 찍는다. 그 목록이 비는 종목을 찾고
(6-K/40-F 를 내는 외국 회사, 2.02 를 안 쓰는 회사, CIK 없음), 대안 소스인
Finnhub 종목별 실적 캘린더가 과거 발표일을 얼마나 주는지 잰다.
"""
import datetime as dt
import os
import sys
import time
from collections import Counter

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nongaap  # noqa: E402
import sources  # noqa: E402

RAW = ("https://raw.githubusercontent.com/animsam09-star/Daily-Express/"
       "data-us/data/overviews_us.json")


def main():
    tickers = list((requests.get(RAW, timeout=30).json() or {}).get("items") or {})
    print(f"종목 {len(tickers)}개")
    ciks = nongaap._ciks()
    since = dt.date.today() - dt.timedelta(days=420)
    rows = []
    for t in tickers:
        cik = ciks.get(t)
        if cik is None:
            rows.append((t, "CIK없음", 0, {}))
            continue
        try:
            sub = nongaap._sec_get(nongaap.SUBMISSIONS.format(cik=cik)).json()
        except Exception as e:                     # noqa: BLE001
            rows.append((t, f"오류 {type(e).__name__}", 0, {}))
            continue
        r = (sub.get("filings") or {}).get("recent") or {}
        forms = Counter()
        n202 = 0
        for form, date, items in zip(r.get("form", []), r.get("filingDate", []),
                                     r.get("items", [])):
            if dt.date.fromisoformat(date) < since:
                break
            forms[form] += 1
            if form in ("8-K", "8-K/A") and "2.02" in (items or ""):
                n202 += 1
        kind = ("6-K" if forms.get("6-K") else
                "40-F" if forms.get("40-F") else
                "20-F" if forms.get("20-F") else "국내")
        rows.append((t, kind, n202, dict(forms)))
        time.sleep(0.12)

    miss = [x for x in rows if x[2] < 3]
    print(f"\n실적 8-K 3건 미만(최근 420일) — {len(miss)}종목")
    for t, kind, n, forms in miss:
        top = ", ".join(f"{k}{v}" for k, v in sorted(forms.items(), key=lambda kv: -kv[1])[:6])
        print(f"  {t:6s} {kind:8s} 2.02={n}  [{top}]")
    print("\n분포:", Counter(k for _, k, n, _ in miss))

    key = sources._finnhub_key()
    if not key:
        print("FINNHUB 키 없음 — 대안 소스 측정 생략")
        return
    print("\nFinnhub 종목별 실적 캘린더(최근 2년)")
    frm = (dt.date.today() - dt.timedelta(days=730)).isoformat()
    to = dt.date.today().isoformat()
    for t, kind, n, _ in miss:
        try:
            r = requests.get(sources.EARN_URL, timeout=20,
                             params={"token": key, "symbol": t, "from": frm, "to": to})
            got = (r.json() or {}).get("earningsCalendar") or [] if r.status_code == 200 else None
        except Exception as e:                     # noqa: BLE001
            got = None
            print(f"  {t}: 실패 {type(e).__name__}")
            continue
        if got is None:
            print(f"  {t}: HTTP {r.status_code}")
            continue
        ds = sorted(x.get("date") for x in got)
        print(f"  {t:6s} {len(ds)}건  {ds[:2]} … {ds[-3:]}  "
              f"eps={[x.get('epsActual') for x in got][:2]}")
        time.sleep(1.1)


if __name__ == "__main__":
    main()
