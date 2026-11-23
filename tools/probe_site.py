"""배포된 페이지의 관심종목 카드(초동·추세·반등)와 '왜 걸렸나' 확인."""
import json
import re

import requests

BASE = "https://daily-express-animsam09-star.pages.dev/"
for page in ("us.html", "kr.html", "index.html"):
    try:
        r = requests.get(BASE + page, timeout=30)
    except Exception as e:                          # noqa: BLE001
        print(page, type(e).__name__)
        continue
    t = r.text
    print(f"== {page} HTTP {r.status_code} {len(t)}B")
    m = re.search(r"const D\s*=\s*(\{.*?\});\s*\n", t, re.S)
    if not m:
        print("  D 없음", re.findall(r"생성[^<]{0,60}", t)[:2])
        continue
    D = json.loads(m.group(1))
    print("  생성:", D.get("generated") or D.get("asof") or D.get("date"))
    hold = {h["ticker"]: h for s in D.get("sectors", []) for h in s.get("holdings", [])}
    for w in D.get("watch", []):
        if w["kind"] == "watch":
            continue
        h = hold.get(w["ticker"], {})
        print(f"  {w['kind']:7s} {w['ticker']:8s} {h.get('name','')[:20]:20s} why={'있음' if h.get('why') else '없음'} {h.get('why','')[:120]}")
