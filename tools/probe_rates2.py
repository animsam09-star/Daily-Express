# -*- coding: utf-8 -*-
"""국내 금리 대체 출처를 찾는다(탐색용).

쓰던 네이버 일별시세 페이지가 410 Gone 이 됐다. 주소를 지어내지 않으려고,
살아 있는 페이지가 실제로 무엇을 부르는지부터 본다.
"""
import re
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
      "Accept-Language": "ko-KR,ko;q=0.9"}


def note(s):
    print("::notice::[금리2] " + str(s).replace("\n", " ")[:900])


def get(url, **kw):
    try:
        r = requests.get(url, headers={**UA, **kw.pop("headers", {})},
                         timeout=20, **kw)
        return r
    except Exception as e:                         # noqa: BLE001
        note(f"{url[:70]} !{type(e).__name__}")
        return None


def main():
    # 1) 네이버 시장지표 메인이 지금 무엇을 가리키는가
    r = get("https://finance.naver.com/marketindex/")
    if r is not None:
        r.encoding = "euc-kr"
        note(f"네이버 시장지표 HTTP {r.status_code} len {len(r.text)}")
        hits = sorted(set(re.findall(r'[\'"]([^\'"]*(?:interest|IRR_)[^\'"]*)[\'"]',
                                     r.text)))
        note(f"  interest/IRR 참조 {len(hits)}개: {hits[:6]}")

    # 2) 모바일 쪽 후보 — 재무제표에 쓰는 m.stock.naver.com 과 같은 계열
    for u in ("https://m.stock.naver.com/api/marketindex/interest/IRR_GOVT03Y",
              "https://m.stock.naver.com/api/marketindex/interest/IRR_GOVT03Y/prices?page=1&pageSize=60",
              "https://api.stock.naver.com/marketindex/interest/IRR_GOVT03Y",
              "https://api.stock.naver.com/marketindex/interest/IRR_GOVT03Y/prices?page=1&pageSize=60"):
        r = get(u)
        if r is not None:
            note(f"{u.split('naver.com')[1][:70]} HTTP {r.status_code} "
                 f"len {len(r.text)} head {r.text[:140]!r}")

    # 3) 한국경제 데이터센터 — KR3Y 로 국고 3년을 준다고 한다
    r = get("https://datacenter.hankyung.com/rates-bonds")
    if r is not None:
        note(f"한경 데이터센터 HTTP {r.status_code} len {len(r.text)}")
        api = sorted(set(re.findall(r'[\'"](/?[a-z0-9/_.-]*api[a-z0-9/_.-]*)[\'"]',
                                    r.text, re.I)))
        note(f"  api 후보 {len(api)}개: {api[:8]}")
        note(f"  KR3Y 등장 {r.text.count('KR3Y')}회 · "
             f"국고 {r.text.count('국고')}회 · AA- {r.text.count('AA-')}회")
    return 0


if __name__ == "__main__":
    sys.exit(main())
