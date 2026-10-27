# -*- coding: utf-8 -*-
"""한경 상세 페이지에 기간 데이터가 있는지, ECOS 는 키가 필요한지 본다."""
import re
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
      "Accept-Language": "ko-KR,ko;q=0.9"}


def note(s):
    print("::notice::[금리5] " + str(s).replace("\n", " ")[:900])


def get(url):
    try:
        return requests.get(url, headers=UA, timeout=20)
    except Exception as e:                         # noqa: BLE001
        note(f"{url[:80]} !{type(e).__name__}")
        return None


def main():
    d = get("https://datacenter.hankyung.com/rates-bonds/kr3y")
    if d is not None:
        t = d.text
        hrefs = sorted({h for h in re.findall(r'href="([^"]+)"', t)
                        if "rates-bonds" in h or "bond" in h.lower()})
        note(f"상세 안 링크 {len(hrefs)}개: {[h[-46:] for h in hrefs][:8]}")
        forms = re.findall(r'<form[^>]*action="([^"]*)"', t)
        note(f"form {forms[:4]} · input name "
             f"{sorted(set(re.findall(r'<input[^>]+name=.([a-zA-Z_]+)', t)))[:10]}")
        # 날짜와 숫자가 나란히 오는 표가 있는가
        tr = re.findall(r"(20\d{2}[-./]\d{2}[-./]\d{2})[\s\S]{0,160}?"
                        r'data-value="(-?[\d.]+)"', t)
        note(f"날짜+값 쌍 {len(tr)}개 {tr[:4]}")
        note(f"'기간' {t.count('기간')}회 · '일별' {t.count('일별')}회 · "
             f"'다운로드' {t.count('다운로드')}회 · 'excel' {t.lower().count('excel')}회")

    # 한국은행 ECOS — 회사채 AA- 까지 주는 공식 출처. 키가 필요한지만 본다.
    r = get("https://ecos.bok.or.kr/api/StatisticSearch/sample/json/kr/1/5/"
            "817Y002/D/20261001/20261027/010200000")
    if r is not None:
        note(f"ECOS 샘플키 HTTP {r.status_code} len {len(r.text)} "
             f"head {r.text[:200]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
