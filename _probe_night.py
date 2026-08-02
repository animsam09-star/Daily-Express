"""코스피200 야간선물 무료 데이터 소스 탐색 (임시 — 러너에서 실행).

후보:
1. 야후 파이낸스 심볼 검색/차트 (KOSPI 관련 선물 심볼이 있는가)
2. 네이버 금융 PC — 국내 선물/야간 관련 메뉴가 있는가
3. 네이버 모바일 증권 API — 선물 엔드포인트
4. 프록시 후보: EWY(iShares MSCI Korea, 미국장) — 야간 방향 프록시
"""
import json
import re

import requests

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36")}


def get(name, url, **kw):
    try:
        r = requests.get(url, headers=kw.pop("headers", UA), timeout=15, **kw)
        print(f"\n===== [{name}] {r.status_code} {url}")
        return r
    except Exception as e:                     # noqa: BLE001
        print(f"\n===== [{name}] ERR {type(e).__name__}: {e}")
        return None


# 1) 야후 심볼 검색
for q in ("KOSPI", "KOSPI 200", "K200"):
    r = get(f"yahoo-search:{q}",
            "https://query1.finance.yahoo.com/v1/finance/search"
            f"?q={requests.utils.quote(q)}&quotesCount=15&newsCount=0")
    if r and r.ok:
        for it in r.json().get("quotes", []):
            print(f"  {it.get('symbol'):16} {it.get('quoteType'):10} "
                  f"{it.get('exchange'):8} {it.get('shortname')}")

# 야후 차트 — 후보 심볼 직접 확인
for sym in ("^KS200", "EWY", "KRW=X"):
    r = get(f"yahoo-chart:{sym}",
            f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(sym, safe='')}?range=5d&interval=1d")
    if r and r.ok:
        res = r.json().get("chart", {}).get("result")
        if res:
            m = res[0].get("meta", {})
            print(f"  ok: {m.get('symbol')} {m.get('exchangeName')} last={m.get('regularMarketPrice')} time={m.get('regularMarketTime')}")

# 2) 네이버 PC — 선물/야간 링크 수집
r = get("naver-sise", "https://finance.naver.com/sise/",
        headers={**UA, "Referer": "https://finance.naver.com/"})
if r is not None and r.ok:
    text = r.content.decode("euc-kr", errors="ignore")
    links = sorted(set(re.findall(r'href="([^"]*)"[^>]*>([^<]*(?:선물|야간)[^<]*)<', text)))
    for href, label in links[:30]:
        print(f"  {label.strip():24} {href}")

# 국내 지수선물 시세 페이지 후보들
for code_url in (
    "https://finance.naver.com/sise/sise_index.naver?code=FUT",
    "https://finance.naver.com/sise/sise_index.naver?code=KPI200",
    "https://finance.naver.com/marketindex/",
):
    r = get("naver-page", code_url, headers={**UA, "Referer": "https://finance.naver.com/"})
    if r is not None and r.ok:
        text = r.content.decode("euc-kr", errors="ignore")
        hits = sorted(set(re.findall(r'([^\s<>"]{0,20}야간[^\s<>"]{0,20})', text)))
        print("  '야간' 매치:", hits[:10] if hits else "없음")

# 3) 네이버 모바일 API 후보
for name, url in (
    ("m-futures-list", "https://m.stock.naver.com/api/futures/list?category=domestic"),
    ("m-index-major", "https://m.stock.naver.com/api/index/major?category=KOSPI"),
    ("m-futures-K200", "https://m.stock.naver.com/api/futures/marketIndex"),
    ("m-home-futures", "https://m.stock.naver.com/front-api/home/futures"),
    ("polling-fut", "https://polling.finance.naver.com/api/realtime?query=SERVICE_INDEX:FUT@KPI200"),
):
    r = get(name, url, headers={**UA, "Referer": "https://m.stock.naver.com/"})
    if r is not None and r.ok:
        body = r.text[:600].replace("\n", " ")
        print(f"  {body}")

print("\n프로브 완료")
