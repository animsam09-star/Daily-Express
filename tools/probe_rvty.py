"""레비티 최근 실적 보도자료(8-K 99.1)에서 부문별 매출·영업이익 표를 뽑아 본다(일회성)."""
import re, html, requests
UA = {"User-Agent": "DailyExpress research bot research@daily-express.invalid"}
sub = requests.get("https://data.sec.gov/submissions/CIK0000031791.json", headers=UA, timeout=30).json()
r = sub["filings"]["recent"]
seen = 0
for form, acc, date, items in zip(r["form"], r["accessionNumber"], r["filingDate"], r["items"]):
    if form != "8-K" or "2.02" not in (items or ""):
        continue
    base = f"https://www.sec.gov/Archives/edgar/data/31791/{acc.replace('-','')}/"
    idx = requests.get(base + "index.json", headers=UA, timeout=30).json()
    ex = [f["name"] for f in idx["directory"]["item"] if re.search(r"ex.?99.?1|exhibit99", f["name"], re.I) and f["name"].endswith((".htm", ".html"))]
    if not ex:
        continue
    t = requests.get(base + ex[0], headers=UA, timeout=30).text
    t = html.unescape(re.sub(r"<[^>]+>", " ", t)); t = re.sub(r"[ \t\xa0]+", " ", t)
    print(f"::group::{date} {ex[0]}")
    for kw in ("Life Sciences", "Diagnostics"):
        for m in list(re.finditer(kw, t))[:12]:
            print("|", t[m.start():m.start()+260].replace("\n", " "))
    print("::endgroup::")
    seen += 1
    if seen >= 1:
        break
