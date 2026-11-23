"""회사명으로 상장 티커 찾기 — SEC 목록, 나스닥 스크리너, 야후 검색."""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sources  # noqa: E402

Q = "anthropic"
UA = {"User-Agent": "Daily-Express market brief research@daily-express.invalid"}
d = requests.get("https://www.sec.gov/files/company_tickers.json", headers=UA, timeout=30).json()
print("SEC:", [v for v in d.values() if Q in v["title"].lower()])
try:
    r = requests.get(sources.NASDAQ_SCREENER, headers={**sources.UA, "Accept": "application/json",
                     "Referer": "https://www.nasdaq.com/"}, timeout=40)
    rows = (r.json().get("data") or {}).get("rows") or []
    print("NASDAQ:", [(x["symbol"], x["name"], x.get("marketCap"), x.get("sector")) for x in rows
                      if Q in (x.get("name") or "").lower()])
except Exception as e:  # noqa: BLE001
    print("NASDAQ 실패", e)
r = requests.get("https://query2.finance.yahoo.com/v1/finance/search", params={"q": Q, "quotesCount": 10},
                 headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
print("YAHOO:", [(x.get("symbol"), x.get("shortname"), x.get("exchange"), x.get("quoteType"))
                 for x in (r.json().get("quotes") or [])])
