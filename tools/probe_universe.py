"""유니버스 확장용 실측 프로브 — GitHub Actions 러너에서만 돌린다.

개발 컨테이너는 야후·SSGA 로 나가는 길이 프록시에서 막혀 있어(403),
'후보를 어디까지 넓힐 수 있는지'를 기억이 아니라 실측으로 확인하려면
러너에서 한 번 찍어봐야 한다. 확인 대상:

  1) 야후 quote 응답에 모멘텀에 쓸 필드(52주 등락률 등)가 실제로 오는가
  2) SPDR 섹터 ETF 보유목록 전체(비중 상위 25 밖)에 팔란티어·블룸에너지가 있는가
  3) 있다면 시총 순위 몇 위인가 — 지금 규칙(시총 상위 5)으로 왜 잘리는가
"""
import json
import sys

sys.path.insert(0, ".")

import sources  # noqa: E402

WATCH = {"PLTR": "팔란티어", "BE": "블룸에너지", "VST": "비스트라", "APP": "앱러빈"}


def probe_quote_fields():
    print("=" * 60)
    print("[1] 야후 quote 응답 필드")
    session, crumb = sources._crumb_session()
    if not (session and crumb):
        print("  crumb 실패 — quote API 를 못 씀")
        return
    r = session.get(sources.QUOTE_URL,
                    params={"symbols": ",".join(WATCH), "crumb": crumb},
                    timeout=sources.TIMEOUT)
    r.raise_for_status()
    res = r.json().get("quoteResponse", {}).get("result", [])
    if not res:
        print("  결과 없음")
        return
    keys = sorted(res[0])
    print(f"  필드 {len(keys)}개: {', '.join(keys)}")
    for q in res:
        print("   ", json.dumps({k: q.get(k) for k in (
            "symbol", "marketCap", "regularMarketChangePercent",
            "fiftyTwoWeekChangePercent", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
            "twoHundredDayAverageChangePercent", "fiftyDayAverageChangePercent",
            "averageDailyVolume3Month")}, ensure_ascii=False))


def probe_holdings():
    print("=" * 60)
    print("[2] SPDR 섹터 ETF 보유목록 전체 — 감시 종목이 어디에 있나")
    universe = {}
    for etf, name in sources.SECTORS:
        try:
            hs = sources._top_holdings(etf, n=999)
        except Exception as e:                     # noqa: BLE001
            print(f"  {etf}({name}) 실패: {type(e).__name__}: {e}")
            continue
        universe[etf] = hs
        hit = [h["ticker"] for h in hs if h["ticker"] in WATCH]
        print(f"  {etf}({name}) 보유 {len(hs)}종목"
              + (f"  ← 감시종목 {hit}" if hit else ""))

    print("-" * 60)
    print("[3] 감시 종목의 섹터 내 시총 순위(지금 규칙은 상위 5만 남긴다)")
    for etf, hs in universe.items():
        if not any(h["ticker"] in WATCH for h in hs):
            continue
        quotes = sources.fetch_quotes([h["ticker"] for h in hs])
        ranked = sorted(hs, key=lambda h: (quotes.get(h["ticker"]) or {})
                        .get("market_cap") or 0, reverse=True)
        for i, h in enumerate(ranked, 1):
            if h["ticker"] in WATCH:
                cap = (quotes.get(h["ticker"]) or {}).get("market_cap") or 0
                print(f"  {etf}: {h['ticker']}({WATCH[h['ticker']]}) "
                      f"시총 {cap/1e9:,.0f}B — 섹터 내 {i}/{len(ranked)}위")
        print(f"    (참고) {etf} 시총 상위 5: "
              + ", ".join(h["ticker"] for h in ranked[:5]))

    print("-" * 60)
    print("[4] 감시 종목 중 어느 ETF 에도 없는 것")
    inside = {h["ticker"] for hs in universe.values() for h in hs}
    missing = [f"{t}({n})" for t, n in WATCH.items() if t not in inside]
    print("  " + (", ".join(missing) if missing else "없음 — 전부 커버됨"))


def probe_kr():
    print("=" * 60)
    print("[5] 한국 테마 풀 크기 + KRX 종목 52주 등락률 수신 여부")
    import kr_sources
    pools = kr_sources.get_pools()
    for t, cs in sorted(pools.items(), key=lambda kv: -len(kv[1])):
        print(f"  {t}: 후보 {len(cs)}종목")
    sample = ["005930.KS", "042660.KS", "112040.KQ", "277810.KQ"]
    session, crumb = sources._crumb_session()
    if not (session and crumb):
        print("  crumb 실패")
        return
    r = session.get(sources.QUOTE_URL,
                    params={"symbols": ",".join(sample), "crumb": crumb},
                    timeout=sources.TIMEOUT)
    for q in r.json().get("quoteResponse", {}).get("result", []):
        print("   ", json.dumps({k: q.get(k) for k in (
            "symbol", "marketCap", "fiftyTwoWeekChangePercent",
            "twoHundredDayAverageChangePercent")}, ensure_ascii=False))


if __name__ == "__main__":
    for fn in (probe_quote_fields, probe_holdings, probe_kr):
        try:
            fn()
        except Exception as e:                     # noqa: BLE001
            print(f"!! {fn.__name__} 실패: {type(e).__name__}: {e}")
