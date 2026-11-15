"""야후 미 국채 금리 심볼의 OHLC 가 쓸 만한지(일회성)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sources
for sym in ("^TNX", "^TYX", "^FVX", "2YY=F", "10Y=F", "30Y=F", "^IRX"):
    try:
        o = sources.yahoo_candles(sym)
        m = sources.monthly_candles(sym)
        print(f"{sym}: {len(o)}봉 월{len(m)} 처음 {o[0][0]} 마지막 {o[-1]} "
              f"고저폭평균 {sum(r[2]-r[3] for r in o[-20:])/20:.3f}")
    except Exception as e:
        print(f"{sym}: 실패 {type(e).__name__} {e}")
h = sources.fetch_treasury_history()
print("재무부 마지막:", {k: v[-1] for k, v in h.items()})
