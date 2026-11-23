"""배포 페이지에 특정 종목이 들어갔는지."""
import json
import re

import requests

t = requests.get("https://daily-express-animsam09-star.pages.dev/us.html", timeout=30).text
D = json.loads(re.search(r'<script[^>]*id="data"[^>]*>(.*?)</script>', t, re.S).group(1))
print("updated", D.get("updated"))
for s in D.get("sectors", []):
    for i, h in enumerate(s.get("holdings", [])):
        if h["ticker"] == "ANT":
            print("sector", s.get("symbol"), s.get("name"), "rank", i + 1, "pick", h.get("pick"),
                  "cap", h.get("market_cap"), "price", h.get("price"), "ohlc", len(h.get("ohlc") or []),
                  "q", len(h.get("quarters") or []), "summary", bool((h.get("profile") or {}).get("summary_ko")))
print("watch", [w for w in D.get("watch", []) if w["ticker"] == "ANT"])
