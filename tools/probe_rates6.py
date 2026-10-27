# -*- coding: utf-8 -*-
"""ECOS 에서 어떤 금리를 받을 수 있는지, 샘플 키로 몇 행까지 되는지 본다."""
import os
import re
import sys

import requests

T = 25
KEY = (os.environ.get("ECOS_API_KEY") or "sample").strip()


def note(s):
    print("::notice::[ECOS] " + str(s).replace("\n", " ")[:900])


def get(path):
    try:
        r = requests.get("https://ecos.bok.or.kr/api/" + path, timeout=T)
        return r
    except Exception as e:                         # noqa: BLE001
        note(f"!{type(e).__name__}")
        return None


def main():
    note(f"키 출처: {'시크릿' if KEY != 'sample' else '샘플(미등록)'}")

    # 1) 시장금리(일별) 에 어떤 항목이 있는가 — 회사채 AA- 코드를 찾는다
    r = get(f"StatisticItemList/{KEY}/json/kr/1/100/817Y002")
    if r is not None:
        note(f"항목목록 HTTP {r.status_code} len {len(r.text)}")
        pairs = re.findall(r'"ITEM_CODE":"([^"]+)","ITEM_NAME":"([^"]+)"', r.text)
        note(f"  항목 {len(pairs)}개")
        for i in range(0, min(len(pairs), 24), 4):
            note("  " + " | ".join(f"{c}={n}" for c, n in pairs[i:i + 4]))

    # 2) 2년치를 한 번에 받을 수 있는가(샘플 키는 행 수가 묶여 있다고 알려져 있다)
    r = get(f"StatisticSearch/{KEY}/json/kr/1/1000/817Y002/D/20241001/20261027/010200000")
    if r is not None:
        total = re.search(r'"list_total_count":(\d+)', r.text)
        rows = len(re.findall(r'"TIME":"', r.text))
        note(f"2년 요청 HTTP {r.status_code} · 전체 {total.group(1) if total else '?'}행 "
             f"· 받은 행 {rows} · len {len(r.text)}")
        if rows:
            last = re.findall(r'"TIME":"(\d+)","DATA_VALUE":"([^"]+)"', r.text)[-3:]
            note(f"  꼬리: {last}")
        if '"RESULT"' in r.text:
            note("  응답 메시지: " + re.sub(r"\s+", " ", r.text[:260]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
