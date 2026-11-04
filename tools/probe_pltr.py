# -*- coding: utf-8 -*-
"""팔란티어(PLTR)가 어느 단계에서 빠지는지 러너에서 본다(탐색용).

코드상 PLTR 은 WATCHLIST 에 XLK 로 고정돼 있어 늘 실려야 하는데 화면에
안 나온다. 스크리너 분류·ETF 보유목록·상위 N 자르기 중 어디서 빠지는지 본다.
"""
import sys

sys.path.insert(0, ".")
import sources as S                                # noqa: E402

T = "PLTR"


def note(s):
    print("::notice::[PLTR] " + str(s).replace("\n", " ")[:900])


def main():
    note(f"WATCHLIST={S.WATCHLIST.get(T)} · CURATED_SECTOR={S.CURATED_SECTOR.get(T)} "
         f"· EXCLUDE_FROM_OTHERS 포함? {T in S.EXCLUDE_FROM_OTHERS} "
         f"· WEB_TOP_N={S.WEB_TOP_N}")

    # 1) 나스닥 스크리너에 있는가, 어느 섹터로 분류되는가
    uni = S.fetch_market_universe()
    where = [(sec, i, r) for sec, rows in uni.items()
             for i, r in enumerate(rows) if r["ticker"] == T]
    if not where:
        note("스크리너 전종목에서 안 보인다 — 이름 필터나 섹터 매핑에서 빠졌다")
    for sec, i, r in where:
        note(f"스크리너: 섹터 {sec} · 그 섹터 안 시총 {i+1}위 · "
             f"시총 {r['market_cap']/1e9:.0f}B · 거래량 {r['volume']:,.0f}")

    # 2) XLK ETF 보유목록에 있는가
    hs = S._top_holdings("XLK", n=999)
    idx = [i for i, h in enumerate(hs) if h["ticker"] == T]
    note(f"XLK 보유목록 {len(hs)}종 · PLTR "
         + (f"있다 #{idx[0]+1}" if idx else "없다"))

    # 3) 전용 섹터로 옮긴 종목을 뺀 뒤 시총 순 상위 N 에 드는가
    caps = {r["ticker"]: r["market_cap"] for rows in uni.values() for r in rows}
    kept = [h for h in hs if h["ticker"] not in S.EXCLUDE_FROM_OTHERS]
    kept.sort(key=lambda h: caps.get(h["ticker"]) or 0, reverse=True)
    top = [h["ticker"] for h in kept[:S.WEB_TOP_N]]
    note(f"XLK 상위 {S.WEB_TOP_N}: {top}")
    rank = next((i + 1 for i, h in enumerate(kept) if h["ticker"] == T), None)
    note(f"PLTR 의 XLK 내 시총 순위 {rank} (시총 {caps.get(T, 0)/1e9:.0f}B) — "
         f"상위 {S.WEB_TOP_N} 안에 {'있다' if T in top else '없다'}")
    product_path()
    return 0


if __name__ == "__main__":
    sys.exit(main())


def product_path():
    """제품이 실제로 쓰는 경로 그대로 돌려 PLTR 이 끝까지 남는지 본다."""
    uni = S.fetch_market_universe()
    caps = {r["ticker"]: r["market_cap"] for rows in uni.values() for r in rows}
    hold = S.fetch_sector_holdings(["XLK"], caps=caps)
    rows = hold.get("XLK") or []
    note(f"fetch_sector_holdings(XLK) -> {len(rows)}종: "
         f"{[h['ticker'] for h in rows]}")
    me = next((h for h in rows if h["ticker"] == T), None)
    note("PLTR " + (f"남아 있다 — 주가 {me.get('price')} · 시총 {me.get('market_cap')} "
                    f"· 등락 {me.get('chg_pct')} · 시계열 {len(me.get('ohlc') or [])}봉"
                    if me else "여기서 사라졌다"))
