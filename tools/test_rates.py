# -*- coding: utf-8 -*-
"""국내 금리 수집을 시험한다 — ECOS 경로와 한경 폴백.

네이버 페이지가 410 Gone 이 된 것을 아무도 몰랐다. 값이 없으면 카드를
조용히 건너뛰었기 때문이다. 바깥 호출 없이, 실측한 응답 모양을 그대로
재현해 파싱과 폴백만 본다.
"""
import datetime as dt
import sys

sys.path.insert(0, ".")
import sources                                     # noqa: E402

# ECOS 실측 응답 모양
ITEMS = ('{"StatisticItemList":{"row":['
         '{"ITEM_CODE":"010101000","ITEM_NAME":"한국은행 기준금리"},'
         '{"ITEM_CODE":"010200000","ITEM_NAME":"국고채(3년)"},'
         '{"ITEM_CODE":"010210000","ITEM_NAME":"국고채(10년)"},'
         '{"ITEM_CODE":"010320000","ITEM_NAME":"회사채(3년, AA-)"}]}}')


def search(vals):
    rows = ",".join('{"TIME":"%s","DATA_VALUE":"%s"}' % (t, v) for t, v in vals)
    return '{"StatisticSearch":{"list_total_count":%d,"row":[%s]}}' % (len(vals), rows)


# 한경 실측 모양 — 심볼 다음에 값, 그다음 전일비가 data-value 로 온다
HK = """
<td><span class="txt-en">KR3Y</span></td>
<td class="txt-rt"><span class="txt-num" data-value="3.79">3.79</span></td>
<td class="txt-rt" data-value="-8.01"></td>
<td><span class="txt-en">KR10Y</span></td>
<td class="txt-rt"><span class="txt-num" data-value="4.06">4.06</span></td>
<td class="txt-rt" data-value="-2.5"></td>
<td><span class="txt-en">CD91</span></td>
<td class="txt-rt"><span class="txt-num" data-value="3.26">3.26</span></td>
<td class="txt-rt" data-value="0"></td>
"""


class R:
    status_code = 200

    def __init__(self, text):
        self.text = text


def check(name, got, want):
    ok = got == want
    print(("OK  " if ok else "FAIL") + f"  {name}")
    if not ok:
        print(f"      got={got!r}\n      want={want!r}")
    return ok


def main():
    ok = True
    g = [("20261023", "3.80"), ("20261026", "3.87"), ("20261027", "3.79")]
    c = [("20261023", "4.20"), ("20261026", "4.27"), ("20261027", "4.22")]

    def fake(url, **kw):
        if "StatisticItemList" in url:
            return R(ITEMS)
        if "/010200000" in url:
            return R(search(g))
        if "/010320000" in url:
            return R(search(c))
        return R(HK)

    saved_get, saved_key = sources.requests.get, sources._ecos_key
    sources.requests.get = fake

    # --- ECOS 경로
    sources._ecos_key = lambda: "KEY"
    try:
        out = sources.fetch_domestic()
    finally:
        sources._ecos_key = saved_key
    ok &= check("회사채 항목을 이름으로 찾는다",
                sources._ecos_items("KEY").get("corp_aa3y"), "010320000")
    ok &= check("국고채 3년 마지막 값", out["govt_3y"]["last"], 3.79)
    ok &= check("전일비는 bp", round(out["govt_3y"]["chg"], 1), -8.0)
    ok &= check("시계열 길이", len(out["govt_3y"]["series"]), 3)
    ok &= check("가장 오래된 날짜가 앞",
                out["govt_3y"]["series"][0][0], dt.date(2026, 10, 23))
    ok &= check("스프레드(회사채-국고채, bp)",
                round(out["spread"]["last"], 0), 43.0)

    # --- 한경 폴백(키 없음)
    sources._ecos_key = lambda: ""
    try:
        out = sources.fetch_domestic()
    finally:
        sources._ecos_key = saved_key
        sources.requests.get = saved_get
    ok &= check("키가 없으면 한경 현재가", out["govt_3y"]["last"], 3.79)
    ok &= check("한경 전일비", out["govt_3y"]["chg"], -8.01)
    ok &= check("한경에는 시계열이 없다", out["govt_3y"]["series"], [])
    ok &= check("국고 10년·CD91 도 받는다",
                (out["govt_10y"]["last"], out["cd_91"]["last"]), (4.06, 3.26))
    ok &= check("한경에는 회사채 AA- 가 없다", "corp_aa3y" in out, False)

    print("\n통과" if ok else "\n실패")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
