"""한국 수집원 후보를 찔러보고 결과만 로그에 남긴다(일회성 진단 도구).

네이버 금융이 Next.js 로 개편되어 기존 HTML 스크래핑이 전부 0건이 됐다
(실측 2026-09-17: 요청은 200, 파싱은 0건). 새 수집원을 정하려면 무엇이
돌아오는지 봐야 하는데 개발 환경은 프록시가 네이버·KRX 를 막는다.
그래서 러너에서 돌리고 응답 앞부분만 로그로 회수한다.

출력은 실행 맨 끝에 몰아서 낸다 — 로그 회수가 tail 만 되기 때문이다.
"""
import json
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"}
NAVER = {**UA, "Referer": "https://finance.naver.com/",
         "Accept": "application/json, text/plain, */*"}
SNIP = 600
TIMEOUT = 15

# (이름, URL, 헤더). 순서는 기대치 높은 것부터.
CANDIDATES = [
    # --- 네이버 새 JSON API 후보 ---
    ("네이버 업종 목록",
     "https://m.stock.naver.com/api/stocks/industry", NAVER),
    ("네이버 업종 상세(반도체 예시)",
     "https://m.stock.naver.com/api/stocks/industry/G2510?page=1&pageSize=60", NAVER),
    ("네이버 종목 기본정보(삼성전자)",
     "https://m.stock.naver.com/api/stock/005930/basic", NAVER),
    ("네이버 종목 통합(삼성전자)",
     "https://m.stock.naver.com/api/stock/005930/integration", NAVER),
    ("네이버 ETF 구성종목(TIGER 로봇)",
     "https://m.stock.naver.com/api/stock/445290/etfComponent", NAVER),
    ("네이버 api.stock 업종",
     "https://api.stock.naver.com/industry", NAVER),
    # --- KRX 공식 ---
    ("KRX 전종목 시세(MDCSTAT01501)",
     "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd", None),
]

KRX_FORM = {
    "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
    "mktId": "ALL",
    "trdDd": "20260916",
    "share": "1",
    "money": "1",
    "csvxls_isNo": "false",
}
KRX_HDR = {**UA, "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd"}

out = []


def note(*a):
    out.append(" ".join(str(x) for x in a))


def body_snip(r):
    t = (r.text or "")
    return " ".join(t.split())[:SNIP]


for name, url, hdr in CANDIDATES:
    try:
        if hdr is None:                      # KRX 는 POST 폼
            r = requests.post(url, data=KRX_FORM, headers=KRX_HDR, timeout=TIMEOUT)
        else:
            r = requests.get(url, headers=hdr, timeout=TIMEOUT)
    except Exception as e:                   # noqa: BLE001
        note(f"[{name}] 실패: {type(e).__name__}: {e}")
        continue
    ct = r.headers.get("content-type", "?")
    note(f"[{name}] HTTP {r.status_code} / {ct} / {len(r.text)}자")
    # JSON 이면 구조를 요약해 준다 — 본문 600자보다 훨씬 쓸모 있다.
    try:
        d = r.json()
    except ValueError:
        note(f"  본문: {body_snip(r)}")
        continue
    if isinstance(d, dict):
        note(f"  JSON dict, 키: {list(d)[:15]}")
        for k in list(d)[:3]:
            v = d[k]
            if isinstance(v, list) and v:
                note(f"  d[{k!r}] 리스트 {len(v)}건, 첫 항목: "
                     f"{json.dumps(v[0], ensure_ascii=False)[:400]}")
    elif isinstance(d, list):
        note(f"  JSON list {len(d)}건, 첫 항목: "
             f"{json.dumps(d[0], ensure_ascii=False)[:400] if d else '(빈 리스트)'}")

print("\n".join(["", "=" * 60, "[수집원 탐색 결과]"] + out + ["=" * 60]))
sys.stdout.flush()
