"""유니버스 확장 실측 프로브 — GitHub Actions 러너에서만 돌린다.

개발 컨테이너는 야후·SSGA·나스닥으로 나가는 길이 프록시에서 막혀 있어(403),
실제 데이터로 확인하려면 러너에서 찍어봐야 한다.

앞선 프로브에서 확인한 것:
  1차 — 팔란티어는 XLK 안에 있으나 섹터 내 시총 12/76위라 '상위 5' 규칙에 잘린다
        (앱러빈 8/24, 비스트라 8/34 도 동일). 블룸에너지는 섹터 ETF 어디에도 없다.
        quote 응답에 52주 등락률·200일선 이격·50일선 이격이 전부 온다(한국 포함).
  2차 — SSGA 광범위 ETF(SPTM·SPMD·SPSM·MDY·SPY)를 다 뒤져도 블룸에너지가 없다.
        S&P 1500 미편입이라 SSGA 경로로는 닿지 않는다.
  3차 — 나스닥 스크리너 API 가 미국 상장 7,113종목을 시총·섹터와 함께 한 번에 준다.
        블룸에너지도 여기 있다(시총 64.3B, 에너지). 시총 100억달러 이상 992종목.

이 파일은 그 결론으로 만든 주도주 선정이 실제 데이터에서 어떻게 뽑히는지 본다.
"""
import sys

sys.path.insert(0, ".")

import sources  # noqa: E402


def probe_us():
    print("=" * 64)
    print("[미국] 섹터별 시총 상위 5 + 주도주")
    universe = sources.fetch_market_universe()
    print(f"  전체 유니버스: {sum(len(v) for v in universe.values())}종목 / "
          f"{len(universe)}섹터")
    holdings = sources.fetch_sector_holdings([s for s, _ in sources.SECTORS])
    extras = sources.pick_extras(holdings, universe)
    for sym, name in sources.SECTORS:
        core = ", ".join(h["ticker"] for h in holdings.get(sym, []))
        print(f"  {name}({sym})")
        print(f"    시총 상위 5 : {core}")
        for p in extras.get(sym, []):
            cap = (p.get("market_cap") or 0) / 1e9
            print(f"    + {p['pick']:<8} {p['ticker']:<6} {p['name'][:34]:<34} "
                  f"시총 {cap:>6,.0f}B  1Y {p.get('chg_52w')}  "
                  f"200일선 {p.get('vs_200d')}")


def probe_kr():
    print("=" * 64)
    print("[한국] 테마별 시총 상위 5 + 주도주")
    import kr_sources
    sectors, holdings = kr_sources.fetch_sectors_and_holdings()
    for s in sectors:
        hs = holdings.get(s["symbol"]) or []
        core = ", ".join(h["name"] for h in hs if not h.get("pick"))
        print(f"  {s['symbol']}")
        print(f"    시총 상위 5 : {core}")
        for h in hs:
            if h.get("pick"):
                cap = (h.get("market_cap") or 0) / 1e12
                print(f"    + {h['pick']:<8} {h['name'][:16]:<16} "
                      f"시총 {cap:>5,.1f}조  1Y {h.get('chg_52w')}  "
                      f"200일선 {h.get('vs_200d')}")


if __name__ == "__main__":
    for fn in (probe_us, probe_kr):
        try:
            fn()
        except Exception as e:                     # noqa: BLE001
            import traceback
            print(f"!! {fn.__name__} 실패: {type(e).__name__}: {e}")
            traceback.print_exc()
