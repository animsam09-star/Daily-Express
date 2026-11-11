"""nongaap 파서·분기 맞춤 점검(네트워크 없이)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nongaap as N

ok = 0
def check(c, msg):
    global ok
    assert c, msg
    ok += 1

# 1) 단위 환산과 상식 검사
out = N._parse('''{"period_end":"2026-10-04","unit":"thousands",
 "cur":{"revenue":728899,"op_gaap":95000,"op_adj":209321,"net_gaap":60000,"net_adj":160000,"eps_adj":1.37},
 "prior":{"period_end":"2025-09-28","revenue":698949,"op_adj":182416,"eps_adj":1.18}}''')
r = out["rows"]
check(r[0]["op_adj"] == 209321e3 and r[0]["eps_adj"] == 1.37, "단위 환산")
check(r[1]["end"] == "2025-09-28" and r[1]["op_gaap"] is None, "전년 동기")
bad = N._parse('{"period_end":"2026-06-30","unit":"millions","cur":{"revenue":100,"op_adj":5000}}')
check(bad["rows"][0]["op_adj"] is None, "매출보다 큰 조정이익은 버린다")
check(N._parse('{"unit":"millions","cur":{}}') is None, "분기말 없으면 버린다")
check(N._parse("no json") is None, "JSON 아님")

# 1b) 연간: fy·fy_prior, 분기보다 작은 연간 매출은 버림
y = N._parse('''{"period_end":"2025-12-31","unit":"millions",
 "cur":{"revenue":772,"op_adj":250},"prior":{"period_end":"2024-12-31","revenue":729,"op_adj":230},
 "fy":{"period_end":"2025-12-31","revenue":2860,"op_adj":810,"eps_adj":5.1},
 "fy_prior":{"period_end":"2024-12-31","revenue":700,"op_adj":700}}''')
check(len(y["years"]) == 1 and y["years"][0]["op_adj"] == 810e6, "연간 추출·상식 검사")
check(y["fy_checked"] and N._parse('''{"period_end":"2026-06-30","unit":"millions",
 "cur":{"revenue":730,"op_adj":211},"fy":null}''')["years"] == [], "연간 없음")

check(N._scale("USD in millions") == 1e6 and N._scale("dollars") == 1 and N._scale("?") is None, "단위 표기 변형")

# 2) 분기 맞춤: 10/4 → 9/30, 매출 어긋나면 안 붙임, 야후에 없는 분기는 prov_adj
h = {"annuals": [{"date": "2025-12-31", "revenue": 2.85e9}],
     "quarters": [{"date": "2026-06-30", "revenue": 729.7e6},
                  {"date": "2025-09-30", "revenue": 699e6}]}
h["quarters"].sort(key=lambda q: q["date"])
rows = [{"end": "2025-09-28", "revenue": 698.9e6, "op_adj": 182.4e6, "net_adj": None, "eps_adj": 1.18},
        {"end": "2026-07-05", "revenue": 729.7e6, "op_adj": 211e6, "net_adj": 150e6, "eps_adj": 1.18},
        {"end": "2026-10-04", "revenue": 728.9e6, "op_adj": 209.3e6, "op_gaap": 95e6,
         "net_gaap": 60e6, "net_adj": 160e6, "eps_adj": 1.37}]
N.merge(h, {"q": rows, "y": [{"end": "2025-12-31", "revenue": 2.86e9, "op_adj": 8.1e8}]})
q = {x["date"]: x for x in h["quarters"]}
check(q["2025-09-30"]["op_adj"] == 182.4e6, "날짜 며칠 차이 맞춤")
check(q["2026-06-30"]["op_adj"] == 211e6, "52주 회계연도 맞춤")
check(h["annuals"][0]["op_adj"] == 8.1e8, "연간 맞춤")
check(h["prov_adj"]["end"] == "2026-10-04" and h["prov_adj"]["op_adj"] == 209.3e6, "잠정 분기")
h2 = {"quarters": [{"date": "2026-06-30", "revenue": 500e6}]}
N.merge(h2, [{"end": "2026-06-28", "revenue": 729e6, "op_adj": 211e6}])
check("op_adj" not in h2["quarters"][0], "매출 어긋나면 붙이지 않는다")

# 3) 발췌는 머리말 + 조정치 구간을 담고 상한을 지킨다
text = "Q3 results headline. " * 200 + ("filler " * 3000) + \
       "Reconciliation: Adjusted operating income $209.3 million non-GAAP " + ("x " * 3000)
s = N._snippet(text)
check("Adjusted operating income" in s and len(s) <= N.SNIP_MAX + 50, "발췌")
print(f"{ok}/{ok} 통과")
