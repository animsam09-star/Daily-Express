"""ITA 가 하루에 -68% 로 찍힌 원인 확인(일회성)."""
import datetime as dt, json, requests
UA = {"User-Agent": "Mozilla/5.0"}
for sym in ("ITA", "XAR"):
    u = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
         "?range=1mo&interval=1d&events=split,div")
    r = requests.get(u, headers=UA, timeout=30).json()["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose") or []
    print(f"::group::{sym}")
    print("events:", json.dumps(r.get("events"), default=str)[:800])
    for i, ts in enumerate(r["timestamp"][-12:]):
        j = len(r["timestamp"]) - 12 + i
        print(dt.datetime.utcfromtimestamp(ts).date(), "close", q["close"][j],
              "vol", q["volume"][j], "adj", adj[j] if j < len(adj) else None)
    print("meta price", r["meta"].get("regularMarketPrice"))
    print("::endgroup::")
