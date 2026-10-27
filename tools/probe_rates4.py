# -*- coding: utf-8 -*-
"""한경 데이터센터에서 받을 수 있는 금리 종목과 시계열을 확인한다(탐색용)."""
import re
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
      "Accept-Language": "ko-KR,ko;q=0.9"}
HK = "https://datacenter.hankyung.com/rates-bonds"
ROW = re.compile(
    r'<a href="https://datacenter\.hankyung\.com/rates-bonds/([a-z0-9]+)"[^>]*'
    r'title="([^"]+)"[\s\S]{0,400}?<span class="txt-en">([A-Z0-9]+)</span>'
    r'[\s\S]{0,300}?data-value="(-?[\d.]+)"', re.S)


def note(s):
    print("::notice::[금리4] " + str(s).replace("\n", " ")[:900])


def get(url):
    try:
        return requests.get(url, headers=UA, timeout=20)
    except Exception as e:                         # noqa: BLE001
        note(f"{url[:80]} !{type(e).__name__}")
        return None


def main():
    r = get(HK)
    if r is None:
        return 1
    rows = ROW.findall(r.text)
    seen, out = set(), []
    for slug, name, sym, val in rows:
        if sym in seen:
            continue
        seen.add(sym)
        out.append((sym, name, val, slug))
    note(f"목록 {len(out)}종")
    for i in range(0, min(len(out), 24), 6):
        note("  " + " | ".join(f"{s}={v}({n})" for s, n, v, _ in out[i:i + 6]))

    # 회사채가 이 페이지에 없으면 어디 있는지 단서를 본다
    note(f"'회사채' {r.text.count('회사채')}회 · 'AA' {r.text.count('AA')}회 "
         f"· 'CP' {r.text.count('CP')}회 · '통안' {r.text.count('통안')}회")

    # 상세 페이지에 시계열이 있는가
    d = get(HK + "/kr3y")
    if d is not None:
        t = d.text
        note(f"상세 HTTP {d.status_code} len {len(t)}")
        dates = re.findall(r"20\d{2}[-./]\d{2}[-./]\d{2}", t)
        note(f"  날짜 {len(dates)}개 {dates[:4]}")
        # 차트용 배열이 박혀 있는지
        arr = re.findall(r"(\[\s*\[[^\]]{0,60}\][\s\S]{0,200})", t)
        note(f"  배열 후보 {len(arr)}개 " + (arr[0][:200] if arr else ""))
        for key in ("data:", "series", "chartData", "ajax", "url:"):
            i = t.find(key)
            if i > 0:
                note(f"  {key!r} 주변: " + re.sub(r"\s+", " ", t[i:i + 180]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
