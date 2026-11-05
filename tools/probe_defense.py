"""배포된 us/kr 페이지에서 방산 카드 데이터를 꺼내 본다(일회성 진단)."""
import json, re, requests

BASE = "https://daily-express-animsam09-star.pages.dev"

def short(v):
    if isinstance(v, list):
        return f"[{len(v)}개]" if len(v) > 6 else v
    if isinstance(v, dict):
        return {k: short(x) for k, x in v.items()}
    if isinstance(v, str) and len(v) > 160:
        return v[:160] + "…"
    return v

for page in ("us.html", "kr.html"):
    html = requests.get(f"{BASE}/{page}", timeout=30).text
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
    d = json.loads(m.group(1).replace("<\\/", "</"))
    print(f"::group::{page} updated={d.get('updated')}")
    secs = d.get("sectors") or []
    print("섹터:", [ (s.get("symbol") or s.get("code"), s.get("name") or s.get("label")) for s in secs ])
    for s in secs:
        blob = json.dumps({k: v for k, v in s.items() if k not in ("holdings", "rows")}, ensure_ascii=False)
        if "방산" in blob or s.get("symbol") == "ITA":
            meta = {k: short(v) for k, v in s.items() if k not in ("holdings", "rows")}
            print("META", json.dumps(meta, ensure_ascii=False)[:3000])
            for h in s.get("holdings") or s.get("rows") or []:
                print("ROW", json.dumps({k: short(v) for k, v in h.items()
                                         if k not in ("series", "about", "earn", "overview")},
                                        ensure_ascii=False)[:700])
    print("::endgroup::")
