# -*- coding: utf-8 -*-
"""국내 금리(국고채·회사채)가 왜 화면에 안 뜨는지 러너에서 본다.

build_kr 에는 국고채 3년·회사채 AA- 3년·스프레드 카드가 이미 있는데
화면에는 코스피·코스닥·원/달러만 나온다. 수집이 비는 것으로 보이지만
이 컨테이너에서는 네이버가 막혀 확인이 안 된다.
"""
import re
import sys

import requests

sys.path.insert(0, ".")
import sources                                     # noqa: E402


def note(s):
    print("::notice::[금리] " + str(s).replace("\n", " ")[:900])


def main():
    for code in ("IRR_GOVT03Y", "IRR_CORP03Y"):
        url = sources.NAVER_URL.format(code=code, page=1)
        try:
            r = requests.get(url, headers=sources.NAVER_HDR,
                             verify=sources.VERIFY, timeout=20)
        except Exception as e:                     # noqa: BLE001
            note(f"{code} !{type(e).__name__}")
            continue
        r.encoding = "euc-kr"
        rows = sources.ROW_RE.findall(r.text)
        note(f"{code} HTTP {r.status_code} len {len(r.text)} 지금 정규식 {len(rows)}행 "
             f"{rows[:2]}")
        # 날짜가 아예 없는지, 있는데 값만 못 집는지 가른다
        d = re.findall(r"\d{4}\.\d{2}\.\d{2}", r.text)
        note(f"{code} 날짜 {len(d)}개 {d[:3]} · <table {r.text.count('<table')}개 "
             f"· <td {r.text.count('<td')}개")
        if r.status_code == 200 and not rows:
            i = r.text.find(d[0]) if d else -1
            note(f"{code} 날짜 주변: " + re.sub(r"\s+", " ", r.text[max(0, i - 120):i + 320]))

    out = sources.fetch_domestic()
    for k, v in (out or {}).items():
        note(f"fetch_domestic {k}: 시계열 {len(v.get('series') or [])}개 "
             f"last={v.get('last')} chg={v.get('chg')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
