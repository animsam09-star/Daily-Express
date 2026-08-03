"""기업 개요·분기 실적 소스 확인용 임시 프로브 — 러너에서만 돌린다.

개발 컨테이너는 야후·네이버가 프록시에서 막혀 있어(403) 응답 구조를 실측으로
확인해야 한다. 확인할 것:
  [미국] 야후 quoteSummary 로 기업 개요(assetProfile)와 분기 실적을 받을 수 있나.
         분기가 4개까지만 오면 fundamentals-timeseries 로 5개 이상 받을 수 있나.
  [한국] 네이버 종목 페이지에서 기업 개요와 분기 실적표를 긁을 수 있나.
"""
import json
import re
import sys

sys.path.insert(0, ".")

import requests  # noqa: E402

import sources  # noqa: E402

QS = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{sym}"
TS = ("https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/"
      "timeseries/{sym}")


def probe_us():
    print("=" * 64)
    print("[미국] 야후 quoteSummary")
    session, crumb = sources._crumb_session()
    if not (session and crumb):
        print("  crumb 실패")
        return
    mods = "assetProfile,summaryProfile,earnings,incomeStatementHistoryQuarterly"
    r = session.get(QS.format(sym="AAPL"),
                    params={"modules": mods, "crumb": crumb}, timeout=30)
    print("  status:", r.status_code)
    try:
        res = (r.json().get("quoteSummary", {}).get("result") or [{}])[0]
    except Exception as e:                         # noqa: BLE001
        print("  파싱 실패:", type(e).__name__, e, r.text[:300])
        return
    print("  모듈:", sorted(res))
    prof = res.get("assetProfile") or res.get("summaryProfile") or {}
    summ = (prof.get("longBusinessSummary") or "")
    print(f"  섹터/산업: {prof.get('sector')} / {prof.get('industry')}")
    print(f"  개요 {len(summ)}자: {summ[:220]}...")
    print(f"  직원수: {prof.get('fullTimeEmployees')}  홈페이지: {prof.get('website')}")

    fin = ((res.get("earnings") or {}).get("financialsChart") or {}).get("quarterly") or []
    print(f"  earnings.financialsChart.quarterly {len(fin)}개:")
    for q in fin:
        print("   ", json.dumps({k: (v.get("raw") if isinstance(v, dict) else v)
                                 for k, v in q.items()}, ensure_ascii=False))
    ec = ((res.get("earnings") or {}).get("earningsChart") or {}).get("quarterly") or []
    print(f"  earningsChart.quarterly(EPS) {len(ec)}개:",
          [(q.get("date"), (q.get("actual") or {}).get("raw")) for q in ec])
    inc = (res.get("incomeStatementHistoryQuarterly") or {}).get("incomeStatementHistory") or []
    print(f"  incomeStatementHistoryQuarterly {len(inc)}개:")
    for q in inc[:5]:
        g = lambda k: (q.get(k) or {}).get("raw") if isinstance(q.get(k), dict) else q.get(k)
        print("   ", q.get("endDate", {}).get("fmt"), "매출", g("totalRevenue"),
              "영업익", g("operatingIncome"), "순익", g("netIncome"))

    print("-" * 64)
    print("  fundamentals-timeseries (5분기 이상 가능한지)")
    types = ",".join(f"quarterly{t}" for t in
                     ("TotalRevenue", "OperatingIncome", "NetIncome", "BasicEPS"))
    r2 = session.get(TS.format(sym="AAPL"),
                     params={"symbol": "AAPL", "type": types, "merge": "false",
                             "period1": 1500000000, "period2": 2000000000,
                             "crumb": crumb}, timeout=30)
    print("  status:", r2.status_code)
    try:
        rows = (r2.json().get("timeseries") or {}).get("result") or []
    except Exception as e:                         # noqa: BLE001
        print("  파싱 실패:", type(e).__name__, e, r2.text[:300])
        return
    for row in rows:
        key = [k for k in row if k != "meta" and k != "timestamp"]
        for k in key:
            vals = [(v.get("asOfDate"), (v.get("reportedValue") or {}).get("raw"))
                    for v in (row.get(k) or []) if v]
            print(f"   {k}: {len(vals)}분기  최근 6개 {vals[-6:]}")


NAVER_MAIN = "https://finance.naver.com/item/main.naver?code={code}"
NAVER_COINFO = "https://finance.naver.com/item/coinfo.naver?code={code}"
FNGUIDE = ("https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx"
           "?cmp_cd={code}&finGubun=MAIN&frq=1")


def _get(url):
    r = requests.get(url, headers={**sources.UA,
                                   "Referer": "https://finance.naver.com/"},
                     verify=sources.VERIFY, timeout=30)
    for enc in ("utf-8", "euc-kr"):
        try:
            return r.status_code, r.content.decode(enc)
        except UnicodeDecodeError:
            continue
    return r.status_code, ""


def probe_kr():
    print("=" * 64)
    print("[한국] 네이버 종목 페이지")
    code = "005930"
    st, html = _get(NAVER_MAIN.format(code=code))
    print(f"  main.naver status={st} len={len(html)}")
    i = html.find("기업실적분석")
    print("  '기업실적분석' 위치:", i)
    if i > 0:
        seg = html[i:i + 9000]
        heads = re.findall(r'<th[^>]*>(?:<[^>]+>)*\s*([^<]+?)\s*<', seg)
        print("  표 머리글:", heads[:24])
        rows = re.findall(r'<th[^>]*scope="row"[^>]*>.*?<span[^>]*>([^<]+)</span>.*?</th>(.*?)</tr>',
                          seg, re.S)
        for name, body in rows[:6]:
            nums = re.findall(r'<td[^>]*>\s*([^<]*?)\s*</td>', body)
            print(f"   {name.strip()}: {nums[:12]}")
    st2, html2 = _get(NAVER_COINFO.format(code=code))
    print(f"  coinfo.naver status={st2} len={len(html2)}")
    for pat in ("기업개요", "summary_info", "cmp_comment"):
        print(f"   '{pat}' 포함:", pat in html2)
    st3, html3 = _get(FNGUIDE.format(code=code))
    print(f"  네이버컴프(FnGuide) status={st3} len={len(html3)}")
    if html3:
        j = html3.find("기업개요")
        print("   '기업개요' 위치:", j)
        k = html3.find("cmp_comment")
        if k > 0:
            print("   개요 일부:", re.sub(r"<[^>]+>", " ", html3[k:k + 700])[:300])


if __name__ == "__main__":
    for fn in (probe_us, probe_kr):
        try:
            fn()
        except Exception as e:                     # noqa: BLE001
            import traceback
            print(f"!! {fn.__name__} 실패: {type(e).__name__}: {e}")
            traceback.print_exc()
