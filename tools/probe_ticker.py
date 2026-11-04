# -*- coding: utf-8 -*-
"""특정 종목이 어느 단계에서 빠지는지 러너에서 본다(탐색용).

팔란티어 때와 같은 순서로 찍는다: 스크리너 → 섹터 목록 → 시총 정렬 →
제품 경로(fetch_sector_holdings). 대상은 아래 T·SEC 를 바꿔 쓴다.
"""
import sys

sys.path.insert(0, ".")
import sources as S                                # noqa: E402

T, SEC = "SPCX", "ARKX"


def note(s):
    print(f"::notice::[{T}] " + str(s).replace("\n", " ")[:900])


def main():
    note(f"CURATED_SECTOR={S.CURATED_SECTOR.get(T)} · WATCHLIST={S.WATCHLIST.get(T)} "
         f"· {SEC} 목록 {len(S.CURATED.get(SEC) or {})}종 · WEB_TOP_N={S.WEB_TOP_N}")
    uni = S.fetch_market_universe()
    caps = {r["ticker"]: r["market_cap"] for rows in uni.values() for r in rows}
    where = [(sec, r) for sec, rows in uni.items() for r in rows if r["ticker"] == T]
    note("스크리너: " + (", ".join(f"{sec} 시총 {r['market_cap']/1e9:.0f}B"
                                   for sec, r in where) or "없다"))
    q = S.fetch_quotes([T]).get(T) or {}
    note(f"야후 시세: price={q.get('price')} market_cap={q.get('market_cap')} "
         f"name={q.get('name')}")
    cur = sorted(((caps.get(t) or 0, t) for t in (S.CURATED.get(SEC) or {})), reverse=True)
    note(f"{SEC} 시총 순(스크리너 기준): " + ", ".join(f"{t}:{c/1e9:.0f}B" for c, t in cur))
    hold = S.fetch_sector_holdings([SEC], caps=caps).get(SEC) or []
    note(f"fetch_sector_holdings({SEC}) -> {[h['ticker'] for h in hold]}")
    me = next((h for h in hold if h["ticker"] == T), None)
    note("결과: " + (f"남아 있다 — 주가 {me.get('price')} 시총 {me.get('market_cap')} "
                    f"시계열 {len(me.get('ohlc') or [])}봉" if me else "여기서 사라졌다"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
