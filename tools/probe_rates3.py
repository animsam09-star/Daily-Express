# -*- coding: utf-8 -*-
"""한경 데이터센터·e-나라지표가 금리를 어디서 받아오는지 본다(탐색용).

네이버는 시장지표에서 금리를 통째로 걷어냈다(IRR_ 참조 0개). 한경
데이터센터 페이지에는 KR3Y·국고 가 보이므로, 그 페이지가 부르는 주소를
스크립트까지 따라가 찾는다 — 주소를 지어내지 않기 위해서다.
"""
import re
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
      "Accept-Language": "ko-KR,ko;q=0.9"}
HK = "https://datacenter.hankyung.com/rates-bonds"


def note(s):
    print("::notice::[금리3] " + str(s).replace("\n", " ")[:900])


def get(url, **kw):
    try:
        return requests.get(url, headers=UA, timeout=20, **kw)
    except Exception as e:                         # noqa: BLE001
        note(f"{url[:80]} !{type(e).__name__}")
        return None


def main():
    r = get(HK)
    if r is None:
        return 1
    html = r.text
    note(f"한경 HTTP {r.status_code} len {len(html)}")

    # KR3Y 주변 — 값이 페이지에 직접 박혀 있으면 그걸 읽으면 된다
    for m in list(re.finditer("KR3Y", html))[:2]:
        i = m.start()
        note("KR3Y 주변: " + re.sub(r"\s+", " ", html[max(0, i - 260):i + 260]))

    # 스크립트를 따라가 fetch 주소를 찾는다
    srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)
    note(f"script {len(srcs)}개: {[s.split('/')[-1][:28] for s in srcs][:8]}")
    pat = re.compile(r'["\'](https?://[^"\']+|/[a-z0-9/_.-]{4,})["\']', re.I)
    found = set()
    for s in srcs[:10]:
        u = s if s.startswith("http") else (
            "https://datacenter.hankyung.com" + (s if s.startswith("/") else "/" + s))
        rr = get(u)
        if rr is None or rr.status_code != 200:
            continue
        for cand in pat.findall(rr.text):
            if re.search(r"(bond|rate|interest|quote|chart|series|data)", cand, re.I) \
                    and not re.search(r"\.(png|jpg|svg|css|woff)", cand, re.I):
                found.add(cand)
    note(f"스크립트에서 찾은 주소 후보 {len(found)}개")
    for c in sorted(found)[:10]:
        note("  " + c[:120])

    # e-나라지표 — 공식 통계. 국고채 3년·5년·10년과 회사채 AA-(3년) 을 같이 준다
    r2 = get("https://www.index.go.kr/unity/potal/main/EachDtlPageDetail.do?idx_cd=1073")
    if r2 is not None:
        t = r2.text
        note(f"e-나라지표 HTTP {r2.status_code} len {len(t)} · "
             f"국고채 {t.count('국고채')}회 · 회사채 {t.count('회사채')}회 · "
             f"AA- {t.count('AA-')}회")
    return 0


if __name__ == "__main__":
    sys.exit(main())
