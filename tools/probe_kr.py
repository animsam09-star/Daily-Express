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
#
# 1차 탐색 결과(2026-09-17): /api/stocks/industry 가 200 JSON 이고 업종명이
# 기존 UPJONG_THEME 키와 그대로 일치했다({"no":284,"name":"우주항공과국방"}).
# /basic, /integration 도 200. 남은 건 업종 상세와 ETF 구성종목 경로다.
# 2차는 그 둘을 no(=284, 우주항공과국방 34종목)로 찔러본다.
IND_NO = 284
ETF = "445290"          # TIGER 로봇
CODE = "005930"         # 삼성전자

CANDIDATES = [
    # 3차: 경로는 다 찾았다. 남은 건 분기 실적·기본정보의 내부 모양이다
    # (dict 안이라 구조가 안 찍혔다). 원문을 길게 떠서 필드명을 확인한다.
    ("실적 quarter 원문",
     "https://m.stock.naver.com/api/stock/005930/finance/quarter", NAVER),
    ("종목 basic 원문",
     "https://m.stock.naver.com/api/stock/005930/basic", NAVER),
]
RAW = True          # 3차는 구조 요약 대신 원문을 본다
RAW_CHARS = 2200

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
    if RAW:
        note("  " + " ".join((r.text or "").split())[:RAW_CHARS])
        continue
    # JSON 이면 구조를 요약해 준다 — 본문 600자보다 훨씬 쓸모 있다.
    try:
        d = r.json()
    except ValueError:
        note(f"  본문: {body_snip(r)}")
        continue
    if isinstance(d, dict):
        note(f"  JSON dict, 키: {list(d)[:20]}")
        for k, v in d.items():                 # 리스트는 전부 본다(구성종목이 어디 있는지 모른다)
            if isinstance(v, list) and v:
                note(f"  d[{k!r}] 리스트 {len(v)}건, 첫 항목: "
                     f"{json.dumps(v[0], ensure_ascii=False)[:300]}")
    elif isinstance(d, list):
        note(f"  JSON list {len(d)}건, 첫 항목: "
             f"{json.dumps(d[0], ensure_ascii=False)[:400] if d else '(빈 리스트)'}")

print("\n".join(["", "=" * 60, "[수집원 탐색 결과]"] + out + ["=" * 60]))
sys.stdout.flush()
