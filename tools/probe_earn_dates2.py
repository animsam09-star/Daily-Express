"""E 대안 소스 측정 — ① 야후 실적일(visualization API) ② SEC 6-K 첨부 이름."""
import datetime as dt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nongaap  # noqa: E402
import sources  # noqa: E402

MISS = "SPOT FRO FMX XOM GMAB PHG GFL SAP CCJ NXE OKLO SPCX EMA DNN PUK DB AER TJX".split()
VIS = "https://query1.finance.yahoo.com/v1/finance/visualization"


def yahoo(s, crumb, t):
    body = {"size": 16, "offset": 0, "sortField": "startdatetime", "sortType": "DESC",
            "entityIdType": "earnings",
            "includeFields": ["startdatetime", "epsestimate", "epsactual", "eventtype"],
            "query": {"operator": "and", "operands": [
                {"operator": "eq", "operands": ["ticker", t]},
                {"operator": "eq", "operands": ["eventtype", "2"]}]}}
    r = s.post(VIS, params={"crumb": crumb, "lang": "en-US", "region": "US"},
               json=body, timeout=20)
    if r.status_code != 200:
        return f"HTTP {r.status_code} {r.text[:120]}"
    doc = ((r.json().get("finance") or {}).get("result") or [{}])[0]
    doc = (doc.get("documents") or [{}])[0]
    cols = [c["label"] for c in doc.get("columns") or []]
    return cols, (doc.get("rows") or [])


def main():
    s, crumb = sources._crumb_session()
    print("crumb", bool(crumb))
    for t in MISS:
        try:
            got = yahoo(s, crumb, t) if s else "세션 없음"
        except Exception as e:                     # noqa: BLE001
            got = f"{type(e).__name__}: {e}"
        if isinstance(got, str):
            print(f"  {t}: {got}")
        else:
            cols, rows = got
            print(f"  {t} {cols} {len(rows)}행")
            for r in rows[:10]:
                print("     ", r)
        time.sleep(0.5)

    ciks = nongaap._ciks()
    since = dt.date.today() - dt.timedelta(days=400)
    for t in ("SPOT", "SAP", "DB", "CCJ", "AER", "GMAB"):
        cik = ciks[t]
        sub = nongaap._sec_get(nongaap.SUBMISSIONS.format(cik=cik)).json()
        r = sub["filings"]["recent"]
        print(f"\n{t} 6-K")
        n = 0
        for form, acc, date, desc in zip(r["form"], r["accessionNumber"], r["filingDate"],
                                         r.get("primaryDocDescription", [])):
            if dt.date.fromisoformat(date) < since:
                break
            if form != "6-K":
                continue
            n += 1
            if n > 12:
                break
            try:
                idx = nongaap._sec_get(nongaap.ARCHIVE.format(cik=cik, acc=acc.replace("-", ""))
                                       + "index.json").json()
                names = [f["name"] for f in idx["directory"]["item"]
                         if not f["name"].endswith((".jpg", ".gif", ".png", ".xml", ".json"))
                         and "index" not in f["name"]]
            except Exception as e:                 # noqa: BLE001
                names = [type(e).__name__]
            print(f"  {date} desc={desc!r} {names[:5]}")
            time.sleep(0.15)


if __name__ == "__main__":
    main()
