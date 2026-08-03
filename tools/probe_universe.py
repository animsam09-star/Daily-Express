"""유니버스 확장용 실측 프로브 — GitHub Actions 러너에서만 돌린다.

개발 컨테이너는 야후·SSGA 로 나가는 길이 프록시에서 막혀 있어(403),
기억이 아니라 실측으로 확인하려면 러너에서 찍어봐야 한다.

지금까지 확인한 것:
  1차 — 팔란티어는 XLK 안에 있으나 섹터 내 시총 12/76위라 '상위 5' 규칙에 잘린다
        (앱러빈 8/24, 비스트라 8/34 도 동일). 블룸에너지는 11개 섹터 ETF 어디에도 없다.
        quote 응답에 52주 등락률·200일선 이격·50일선 이격·3개월 평균거래량이 전부 온다.
  2차 — SSGA 광범위 ETF(SPTM 1515·SPMD 403·SPSM 611·MDY 401·SPY 505)를 다 뒤져도
        블룸에너지가 없다. S&P 1500 자체에 미편입이라 SSGA 경로로는 닿지 않는다.
        섹터 열은 MDY 만 GICS 값이 있고 나머지는 전부 '-' 라 매핑에도 못 쓴다.

3차(이 파일) — S&P 밖 종목까지 닿는 전체 시장 소스를 찾는다.
  a) 나스닥 스크리너 API: 미국 상장 전종목 + 시총 + 섹터를 한 번에 주는가
  b) 야후 사전정의 스크리너(day_gainers 등): crumb 으로 접근되는가
"""
import json
import sys

sys.path.insert(0, ".")

import requests  # noqa: E402

import sources  # noqa: E402

WATCH = {"BE": "블룸에너지", "PLTR": "팔란티어", "APP": "앱러빈", "VST": "비스트라"}

NASDAQ_SCREENER = ("https://api.nasdaq.com/api/screener/stocks"
                   "?tableonly=true&limit=25&offset=0&download=true")
YAHOO_SCREENER = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"


def probe_nasdaq():
    print("=" * 60)
    print("[a] 나스닥 스크리너 API — 미국 전종목 + 시총 + 섹터")
    hdr = {**sources.UA, "Accept": "application/json",
           "Referer": "https://www.nasdaq.com/"}
    try:
        r = requests.get(NASDAQ_SCREENER, headers=hdr,
                         verify=sources.VERIFY, timeout=40)
        r.raise_for_status()
        rows = (r.json().get("data") or {}).get("rows") or []
    except Exception as e:                         # noqa: BLE001
        print(f"  실패: {type(e).__name__}: {e}")
        return
    print(f"  {len(rows)}종목 수신")
    if not rows:
        return
    print(f"  열: {sorted(rows[0])}")
    print(f"  샘플: {json.dumps(rows[0], ensure_ascii=False)}")
    idx = {r.get("symbol"): r for r in rows}
    for t, n in WATCH.items():
        r = idx.get(t)
        print(f"  {t}({n}): " + (json.dumps(r, ensure_ascii=False) if r else "없음"))
    secs = {}
    for r in rows:
        s = r.get("sector") or "(빈값)"
        secs[s] = secs.get(s, 0) + 1
    print(f"  섹터 값: {sorted(secs.items(), key=lambda kv: -kv[1])}")
    # 시총 하한을 걸었을 때 몇 종목이 남는지 — 시세 조회 비용을 가늠한다
    def cap(r):
        try:
            return float(str(r.get("marketCap") or "0").replace(",", "") or 0)
        except ValueError:
            return 0.0
    for floor in (5e9, 10e9, 20e9, 50e9):
        n = sum(1 for r in rows if cap(r) >= floor)
        print(f"  시총 {floor/1e9:.0f}B 이상: {n}종목")


def probe_yahoo_screener():
    print("=" * 60)
    print("[b] 야후 사전정의 스크리너")
    session, crumb = sources._crumb_session()
    if not (session and crumb):
        print("  crumb 실패")
        return
    for scr in ("day_gainers", "most_actives"):
        try:
            r = session.get(YAHOO_SCREENER,
                            params={"scrIds": scr, "count": 25, "crumb": crumb},
                            timeout=sources.TIMEOUT)
            r.raise_for_status()
            res = (r.json().get("finance", {}).get("result") or [{}])[0]
            quotes = res.get("quotes") or []
            print(f"  {scr}: {len(quotes)}종목 — "
                  + ", ".join(q.get("symbol", "?") for q in quotes[:12]))
        except Exception as e:                     # noqa: BLE001
            print(f"  {scr} 실패: {type(e).__name__}: {e}")


if __name__ == "__main__":
    for fn in (probe_nasdaq, probe_yahoo_screener):
        try:
            fn()
        except Exception as e:                     # noqa: BLE001
            print(f"!! {fn.__name__} 실패: {type(e).__name__}: {e}")
