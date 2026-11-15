"""CNBC 차트 API 로 미 국채 금리 OHLC 를 받을 수 있는지(일회성)."""
import requests, datetime as dt
UA = {"User-Agent": "Mozilla/5.0"}
for rng in ("1Y", "5Y", "ALL"):
    for sym in ("US2Y", "US10Y", "US30Y"):
        u = f"https://ts-api.cnbc.com/harmony/app/charts/{rng}.json?symbol={sym}"
        try:
            r = requests.get(u, headers=UA, timeout=30)
            j = r.json()
            bars = (j.get("barData") or {}).get("priceBars") or []
            def d(b): return dt.datetime.utcfromtimestamp(int(b["tradeTimeinMills"])/1000).date()
            print(rng, sym, r.status_code, len(bars),
                  (d(bars[0]), d(bars[-1]), bars[-1]) if bars else list(j)[:5])
            if bars:
                gaps = sum(1 for b in bars[-30:] if float(b["high"]) > float(b["low"]))
                print("   최근30봉 중 고저폭 있는 봉", gaps, "간격(일)", (d(bars[-1]) - d(bars[-2])).days)
        except Exception as e:
            print(rng, sym, "실패", type(e).__name__, e)
