"""데이터 수집 — 전부 공개 소스. 각 함수는 (현재값, 전일대비, 2년 시계열)을 돌려준다.

소스 확정 근거(2026-07-28 실측):
  - 미국 지수/환율/섹터 : Yahoo chart API
  - 미국 국채 현재값     : CNBC(Tradeweb) — 소수 3자리, 샘플과 정확 일치
  - 미국 국채 2년 히스토리: 美 재무부 일별 CMT CSV
  - 국내 금리           : 네이버 금융 시장지표(일별시세, 페이지당 7행)
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import re
from concurrent.futures import ThreadPoolExecutor

import requests
import urllib3

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
# 기본은 정상 검증(GitHub Actions). 사내 SSL 검사 프록시 뒤에서 개발할 때만
# SSL_VERIFY=0 으로 끈다.
VERIFY = os.environ.get("SSL_VERIFY", "1") != "0"
if not VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
TIMEOUT = 25

INDICES = [("^DJI", "Dow"), ("^GSPC", "S&P500"), ("^IXIC", "Nasdaq")]

SECTORS = [
    ("XLK", "기술"), ("XLC", "커뮤니케이션"), ("XLY", "경기소비재"),
    ("XLP", "필수소비재"), ("XLE", "에너지"), ("XLF", "금융"),
    ("XLV", "헬스케어"), ("XLI", "산업재"), ("XLB", "소재"),
    ("XLRE", "부동산"), ("XLU", "유틸리티"),
]


def _get(url: str, **kw) -> requests.Response:
    r = requests.get(url, headers=UA, verify=VERIFY, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------- Yahoo
def yahoo_series(symbol: str, rng: str = "2y"):
    """일별 종가 시계열 [(date, close), ...] 을 오래된 것부터 반환."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{requests.utils.quote(symbol, safe='')}?range={rng}&interval=1d"
    )
    res = _get(url).json()["chart"]["result"][0]
    stamps = res["timestamp"]
    closes = res["indicators"]["quote"][0]["close"]
    out = []
    for ts, c in zip(stamps, closes):
        if c is None:
            continue
        out.append((dt.datetime.utcfromtimestamp(ts).date(), float(c)))
    return out


def pct_change(series):
    """직전 거래일 대비 등락률(%)."""
    if len(series) < 2:
        return 0.0
    return (series[-1][1] / series[-2][1] - 1.0) * 100.0


def fetch_indices():
    out = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(yahoo_series, sym): (sym, name) for sym, name in INDICES}
        for f, (sym, name) in futs.items():
            s = f.result()
            out[name] = {"series": s, "last": s[-1][1], "chg_pct": pct_change(s)}
    return out


def fetch_sectors():
    """섹터별 일간 등락률 + 2개년 시계열(상대성과 차트용)."""
    out = []
    with ThreadPoolExecutor(max_workers=11) as ex:
        futs = {ex.submit(yahoo_series, sym): (sym, name) for sym, name in SECTORS}
        for f, (sym, name) in futs.items():
            s = f.result()
            out.append({"symbol": sym, "name": name,
                        "chg_pct": pct_change(s), "series": s})
    out.sort(key=lambda d: d["chg_pct"], reverse=True)
    return out


SSGA_HOLDINGS = (
    "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/"
    "etfs/us/holdings-daily-us-en-{etf}.xlsx"
)
TOP_N = 5


def _yahoo_ticker(t: str) -> str:
    """SSGA 표기를 야후 심볼로. 예: BRK.B -> BRK-B"""
    return t.strip().upper().replace(".", "-")


def _top_holdings(etf: str, n: int = TOP_N):
    """SPDR 일별 보유종목 공시에서 비중 상위 n개(이미 비중순으로 정렬돼 있다)."""
    import openpyxl

    raw = _get(SSGA_HOLDINGS.format(etf=etf.lower())).content
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    try:
        rows = [list(r) for r in wb.active.iter_rows(values_only=True)]
    finally:
        wb.close()

    hi = next((i for i, r in enumerate(rows)
               if r and any(str(c).strip() == "Ticker" for c in r if c)), None)
    if hi is None:
        return []
    hdr = [str(c).strip() if c else "" for c in rows[hi]]
    ti, ni, wi = hdr.index("Ticker"), hdr.index("Name"), hdr.index("Weight")

    out = []
    for r in rows[hi + 1:]:
        if not r or not r[ti]:
            continue
        try:
            weight = float(r[wi])
        except (TypeError, ValueError):
            continue
        out.append({"ticker": _yahoo_ticker(str(r[ti])),
                    "name": str(r[ni]).strip(),
                    "weight": weight})
        if len(out) >= n:
            break
    return out


def fetch_sector_holdings(sector_symbols):
    """섹터별 상위 5개 종목 + 각 종목의 당일 등락률."""
    with ThreadPoolExecutor(max_workers=6) as ex:
        holdings = dict(zip(sector_symbols,
                            ex.map(lambda e: _top_holdings(e), sector_symbols)))

    # 종목은 섹터 간 중복되지 않지만, 중복돼도 한 번만 받도록 모아서 조회한다
    tickers = sorted({h["ticker"] for hs in holdings.values() for h in hs})

    def chg(sym):
        try:
            return sym, pct_change(yahoo_series(sym, rng="5d"))
        except Exception:                      # noqa: BLE001
            return sym, None

    with ThreadPoolExecutor(max_workers=12) as ex:
        moves = dict(ex.map(chg, tickers))

    for hs in holdings.values():
        for h in hs:
            h["chg_pct"] = moves.get(h["ticker"])
    return holdings


def fetch_fx():
    s = yahoo_series("KRW=X")
    diff = s[-1][1] - s[-2][1] if len(s) > 1 else 0.0
    return {"series": s, "last": s[-1][1], "chg": diff}


# ------------------------------------------------------------- 美 국채
CNBC_URL = (
    "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
    "?symbols=US2Y|US10Y|US30Y&requestMethod=itv&noform=1&partnerId=2"
    "&fund=1&exthrs=1&output=json&events=1"
)
TENOR_LABEL = {"US2Y": "2yr", "US10Y": "10yr", "US30Y": "30yr"}
TENOR_CSVCOL = {"US2Y": "2 Yr", "US10Y": "10 Yr", "US30Y": "30 Yr"}


def _num(x):
    """'4.314%' / '-0.009' -> float"""
    if x is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(x).replace(",", ""))
    return float(m.group()) if m else None


def fetch_treasury_now():
    """CNBC 실시간 스냅샷: 금리(%)와 전일대비(bp)."""
    quotes = _get(CNBC_URL).json()["FormattedQuoteResult"]["FormattedQuote"]
    out = {}
    for q in quotes:
        sym = q["symbol"]
        last, chg = _num(q.get("last")), _num(q.get("change"))
        if last is None:
            continue
        out[sym] = {
            "label": TENOR_LABEL[sym],
            "yield": last,
            "chg_bp": (chg or 0.0) * 100.0,   # %p -> bp
            "asof": q.get("last_timedate"),
        }
    return out


def fetch_treasury_history():
    """재무부 일별 CMT CSV에서 2/10/30년 2개년 시계열."""
    this_year = dt.date.today().year
    rows = []
    for y in (this_year - 2, this_year - 1, this_year):
        url = (
            "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
            f"daily-treasury-rates.csv/{y}/all?type=daily_treasury_yield_curve"
            f"&field_tdr_date_value={y}&page&_format=csv"
        )
        try:
            text = _get(url).text
        except Exception:
            continue
        rows.extend(list(csv.DictReader(io.StringIO(text))))

    cutoff = dt.date.today() - dt.timedelta(days=730)
    hist = {sym: [] for sym in TENOR_CSVCOL}
    for row in rows:
        try:
            d = dt.datetime.strptime(row["Date"], "%m/%d/%Y").date()
        except (ValueError, KeyError):
            continue
        if d < cutoff:
            continue
        for sym, col in TENOR_CSVCOL.items():
            v = row.get(col, "").strip()
            if v:
                try:
                    hist[sym].append((d, float(v)))
                except ValueError:
                    pass
    for sym in hist:
        hist[sym].sort()
    return hist


# --------------------------------------------------------------- 국내 금리
NAVER_URL = "https://finance.naver.com/marketindex/interestDailyQuote.naver?marketindexCd={code}&page={page}"
NAVER_HDR = {**UA, "Referer": "https://finance.naver.com/"}
ROW_RE = re.compile(r"(\d{4}\.\d{2}\.\d{2})[\s\S]{0,200}?<td[^>]*>\s*([\d.]+)\s*</td>")


def _naver_page(code: str, page: int):
    r = requests.get(
        NAVER_URL.format(code=code, page=page),
        headers=NAVER_HDR, verify=VERIFY, timeout=TIMEOUT,
    )
    r.encoding = "euc-kr"
    out = []
    for ds, vs in ROW_RE.findall(r.text):
        try:
            out.append((dt.datetime.strptime(ds, "%Y.%m.%d").date(), float(vs)))
        except ValueError:
            pass
    return out


def naver_series(code: str, pages: int = 76):
    """네이버 일별시세(페이지당 7행)를 긁어 2개년 시계열을 만든다."""
    rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for chunk in ex.map(lambda p: _naver_page(code, p), range(1, pages + 1)):
            rows.extend(chunk)
    rows = sorted(set(rows))
    cutoff = dt.date.today() - dt.timedelta(days=730)
    return [(d, v) for d, v in rows if d >= cutoff]


def fetch_domestic():
    """회사채 AA- 3년, 국고채 3년, 그리고 그 스프레드."""
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_corp = ex.submit(naver_series, "IRR_CORP03Y")
        f_govt = ex.submit(naver_series, "IRR_GOVT03Y")
    corp, govt = f_corp.result(), f_govt.result()

    gmap = dict(govt)
    spread = [(d, (v - gmap[d]) * 100.0) for d, v in corp if d in gmap]  # bp

    def snap(series, scale=100.0):
        if not series:
            return {"series": series, "last": None, "chg": None}
        last = series[-1][1]
        chg = (last - series[-2][1]) * scale if len(series) > 1 else 0.0
        return {"series": series, "last": last, "chg": chg}

    return {
        "corp_aa3y": snap(corp),          # chg 단위: bp
        "govt_3y": snap(govt),
        "spread": {**snap(spread, scale=1.0), "unit": "bp"},
    }


# ------------------------------------------------------------------ 전체
def collect_all():
    """모든 지표를 모아 하나의 dict로. 개별 실패는 errors에 남기고 계속 진행한다."""
    data, errors = {}, []

    def run(key, fn):
        try:
            data[key] = fn()
        except Exception as e:                     # noqa: BLE001
            errors.append(f"{key}: {type(e).__name__}: {e}")
            data[key] = None

    run("indices", fetch_indices)
    run("sectors", fetch_sectors)
    run("holdings", lambda: fetch_sector_holdings([s for s, _ in SECTORS]))
    run("fx", fetch_fx)
    run("ust_now", fetch_treasury_now)
    run("ust_hist", fetch_treasury_history)
    run("domestic", fetch_domestic)

    data["errors"] = errors
    data["asof"] = dt.datetime.now().astimezone()
    return data
