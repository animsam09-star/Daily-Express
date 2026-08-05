"""섹터 상위 종목이 정말 시총 순인지 확인하는 임시 프로브 — 러너에서만 돌린다.

의심: XLK 상위 10 에 AMD·CRM·IBM 이 없고 AMAT·LRCX 가 들어와 있다.
후보를 ETF 비중 상위 25 로 자르는 단계에서 빠졌는지, 시세 조회에서 시가총액이
비어 0 으로 밀렸는지를 가른다.
"""
import sys

sys.path.insert(0, ".")

import sources  # noqa: E402

CHECK = {"AMD", "CRM", "IBM", "NOW", "ACN", "QCOM", "TXN", "ADBE", "INTC",
         "AMAT", "LRCX", "PLTR", "ORCL", "CSCO"}


def main():
    print("=" * 64)
    print("[1] XLK 보유목록 — 현재 후보 수(25)와 전량 비교")
    top25 = sources._top_holdings("XLK", n=25)
    allh = sources._top_holdings("XLK", n=999)
    print(f"  비중 상위 25: {[h['ticker'] for h in top25]}")
    print(f"  전량 {len(allh)}종목")
    missing = CHECK - {h["ticker"] for h in top25}
    print(f"  상위 25 후보에서 빠진 관심 종목: {sorted(missing)}")

    print("=" * 64)
    print("[2] 전량 기준 시가총액 순위 — 시세와 스크리너를 비교한다")
    tickers = [h["ticker"] for h in allh]
    quotes = sources.fetch_quotes(tickers)
    universe = sources.fetch_market_universe()
    screener = {r["ticker"]: r["market_cap"]
                for rows in universe.values() for r in rows}

    def cap_q(t):
        return (quotes.get(t) or {}).get("market_cap") or 0

    ranked = sorted(tickers, key=lambda t: max(cap_q(t), screener.get(t, 0)),
                    reverse=True)
    print("  시총 상위 15(둘 중 큰 값 기준):")
    for i, t in enumerate(ranked[:15], 1):
        q, s = cap_q(t), screener.get(t, 0)
        flag = "  <-- 시세에 시총 없음" if not q else ""
        print(f"   {i:2}. {t:6} 시세 {q/1e9:>8,.0f}B  스크리너 {s/1e9:>8,.0f}B{flag}")

    print("-" * 64)
    print("  관심 종목의 순위와 값")
    for t in sorted(CHECK):
        if t in ranked:
            print(f"   {t:6} {ranked.index(t)+1:>3}위  시세 {cap_q(t)/1e9:>8,.0f}B  "
                  f"스크리너 {screener.get(t, 0)/1e9:>8,.0f}B")
        else:
            print(f"   {t:6} XLK 보유목록에 없음")

    print("=" * 64)
    print("[3] 지금 규칙(상위 25 후보 -> 시세 시총 상위 10)의 결과")
    cur = sorted(top25, key=lambda h: cap_q(h["ticker"]), reverse=True)[:10]
    print("  " + ", ".join(h["ticker"] for h in cur))
    print("  전량 후보 + 스크리너 시총으로 뽑으면:")
    print("  " + ", ".join(ranked[:10]))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                         # noqa: BLE001
        import traceback
        print(f"!! 실패: {type(e).__name__}: {e}")
        traceback.print_exc()
