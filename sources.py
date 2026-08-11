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
    ("XLRE", "부동산"), ("XLU", "유틸리티"), ("NLR", "원자력"),
]

# 원자력은 GICS 섹터가 아니라 세 곳에 흩어져 있다 — 원전 발전사(CEG·VST)는
# 유틸리티, 원자로·SMR(BWXT·OKLO·SMR)은 산업재/유틸리티, 우라늄(CCJ·LEU)만
# 에너지다. 그래서 에너지 카드를 아무리 봐도 원자력이 안 보인다.
# 별도 섹터로 세우되, SPDR 이 아니라서 SSGA 구성종목 파일이 없다. 지수·차트는
# NLR(VanEck 원자력 ETF)로 그리고 구성종목은 아래 목록으로 직접 관리한다.
# 순위는 다른 섹터와 똑같이 스크리너 시가총액으로 매긴다.
CURATED = {
    "NLR": {
        "CEG": "Constellation Energy",   # 미국 최대 원전 운영사
        "VST": "Vistra",                 # 원전·가스 발전
        "TLN": "Talen Energy",           # 서스쿼해나 원전
        "CCJ": "Cameco",                 # 우라늄 채굴·정련
        "BWXT": "BWX Technologies",      # 원자로(해군·SMR)
        "OKLO": "Oklo",                  # 소형모듈원자로
        "SMR": "NuScale Power",          # 소형모듈원자로
        "LEU": "Centrus Energy",         # 농축우라늄(HALEU)
        "NNE": "Nano Nuclear Energy",    # 마이크로 원자로
        "UEC": "Uranium Energy",         # 우라늄 채굴
        "NXE": "NexGen Energy",          # 우라늄 개발
        "DNN": "Denison Mines",          # 우라늄 개발
    },
}
CURATED_SECTOR = {t: sym for sym, ts in CURATED.items() for t in ts}


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


def yahoo_ohlc(symbol: str, rng: str = "2y"):
    """일목균형표용 고가·저가·종가. [(date, high, low, close), ...]"""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{requests.utils.quote(symbol, safe='')}?range={rng}&interval=1d"
    )
    res = _get(url).json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    out = []
    for ts, h, l, c in zip(res["timestamp"], q.get("high") or [],
                           q.get("low") or [], q.get("close") or []):
        if None in (h, l, c):
            continue
        out.append((dt.datetime.utcfromtimestamp(ts).date(),
                    float(h), float(l), float(c)))
    return out


def yahoo_candles(symbol: str, rng: str = "2y"):
    """캔들차트용 진짜 OHLC. [(date, open, high, low, close), ...]

    yahoo_ohlc 는 일목균형표용이라 고·저·종만 뽑는다. 캔들은 시가가 있어야
    몸통이 생긴다 — 시가를 직전 종가로 대신하면 몸통이 0 이 되어 꼬리만
    남은 막대 그래프가 된다. 같은 응답에 open 이 들어 있으므로 그냥 쓴다.
    """
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{requests.utils.quote(symbol, safe='')}?range={rng}&interval=1d"
    )
    res = _get(url).json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    out = []
    for ts, o, h, l, c in zip(res["timestamp"], q.get("open") or [],
                              q.get("high") or [], q.get("low") or [],
                              q.get("close") or []):
        if None in (o, h, l, c):
            continue
        out.append((dt.datetime.utcfromtimestamp(ts).date(),
                    float(o), float(h), float(l), float(c)))
    return out


def pct_change(series):
    """직전 거래일 대비 등락률(%)."""
    if len(series) < 2:
        return 0.0
    return (series[-1][1] / series[-2][1] - 1.0) * 100.0


def fetch_indices():
    out = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(yahoo_candles, sym): (sym, name) for sym, name in INDICES}
        for f, (sym, name) in futs.items():
            o = f.result()
            s = [(d, c) for d, _, _, _, c in o]
            out[name] = {"series": s, "ohlc": o, "last": s[-1][1],
                         "chg_pct": pct_change(s),
                         "returns": {k: _return_at(s, d) for k, d in RETURN_WINDOWS}}
    return out


def fetch_sectors():
    """섹터별 일간 등락률 + 2개년 시계열(상대성과 차트용)."""
    out = []
    with ThreadPoolExecutor(max_workers=11) as ex:
        futs = {ex.submit(yahoo_ohlc, sym): (sym, name) for sym, name in SECTORS}
        for f, (sym, name) in futs.items():
            o = f.result()
            s = [(d, c) for d, _, _, c in o]
            out.append({"symbol": sym, "name": name, "ohlc": o,
                        "chg_pct": pct_change(s), "series": s,
                        "returns": {k: _return_at(s, d) for k, d in RETURN_WINDOWS}})
    # 정렬은 collect_all 에서 시가총액 순으로 한다(등락률 순으로 두면 순서가
    # 매일 바뀌어 어제와 대조하기 어렵다).
    return out


SSGA_HOLDINGS = (
    "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/"
    "etfs/us/holdings-daily-us-en-{etf}.xlsx"
)
TOP_N = 5          # 텔레그램 캡션에 넣는 종목 수(길이 제한이 빡빡하다)
WEB_TOP_N = 10     # 웹 대시보드에 싣는 종목 수 — 화면은 길이 제약이 없다


def _yahoo_ticker(t: str) -> str:
    """SSGA 표기를 야후 심볼로. 예: BRK.B -> BRK-B"""
    return t.strip().upper().replace(".", "-")


CLASS_RE = re.compile(r"\b(CL|CLASS|SER|SERIES)\s+[A-Z]\b")
NAME_NOISE = {"INC", "CORP", "CORPORATION", "CO", "THE", "PLC", "LTD", "LLC",
              "COMPANY", "HOLDINGS", "HOLDING", "GROUP", "SA", "NV", "AG"}


def _company_key(name: str) -> str:
    """같은 회사의 복수 클래스를 하나로 묶기 위한 식별자.

    ALPHABET INC CL A 와 ALPHABET INC CL C 는 같은 회사다. 둘 다 상위 5에
    들어가면 한 자리를 낭비하므로 비중이 큰 쪽만 남긴다.
    """
    s = CLASS_RE.sub(" ", name.upper())
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return " ".join(w for w in s.split() if w not in NAME_NOISE)


CANDIDATES = 25       # 시총 상위 5를 제대로 뽑으려면 후보를 넉넉히 봐야 한다


def _top_holdings(etf: str, n: int = CANDIDATES):
    """SPDR 일별 보유종목 공시에서 후보 n개.

    파일은 ETF 비중순으로 정렬돼 있는데 비중은 유동주식 기준이라 시가총액
    순서와 다르다(PG 가 KO 보다 시총이 작은데 앞에 있었다). 여기서는 후보만
    넉넉히 넘기고, 시총 순 상위 5 선정은 시세를 받은 뒤에 한다.
    """
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

    out, seen = [], set()
    # 중복 클래스를 걸러내면 자리가 비므로 넉넉히 읽고 나서 상위 n 개를 고른다
    for r in rows[hi + 1: hi + 1 + n * 3]:
        if not r or not r[ti]:
            continue
        try:
            weight = float(r[wi])
        except (TypeError, ValueError):
            continue
        name = str(r[ni]).strip()
        key = _company_key(name)
        if key in seen:
            continue
        seen.add(key)
        out.append({"ticker": _yahoo_ticker(str(r[ti])),
                    "name": name, "weight": weight})
        if len(out) >= n:
            break
    return out


def _crumb_session():
    """야후 시세 API 는 쿠키+crumb 를 요구한다. 실패하면 None."""
    s = requests.Session()
    s.headers.update(UA)
    s.verify = VERIFY
    try:
        s.get("https://fc.yahoo.com/", timeout=TIMEOUT)
        crumb = s.get("https://query2.finance.yahoo.com/v1/test/getcrumb",
                      timeout=TIMEOUT).text.strip()
    except Exception:                          # noqa: BLE001
        return None, None
    return (s, crumb) if crumb and "<" not in crumb else (None, None)


QUOTE_URL = "https://query2.finance.yahoo.com/v7/finance/quote"
QUOTE_BATCH = 25            # 배치 하나가 실패하면 그만큼 시총이 통째로 빈다


def _quote_batch(session, crumb, batch, out):
    r = session.get(QUOTE_URL,
                    params={"symbols": ",".join(batch), "crumb": crumb},
                    timeout=TIMEOUT)
    r.raise_for_status()
    for q in r.json().get("quoteResponse", {}).get("result", []):
        out[q.get("symbol")] = {
            "price": q.get("regularMarketPrice"),
            "market_cap": q.get("marketCap"),
            "chg_pct": q.get("regularMarketChangePercent"),
            # 주도주 선정용 추세 지표. 같은 응답에 이미 들어 있어 공짜다.
            # 52주 등락률은 %, 이평 이격은 비율(0.22 = +22%)로 온다.
            "chg_52w": q.get("fiftyTwoWeekChangePercent"),
            "vs_200d": q.get("twoHundredDayAverageChangePercent"),
            "vs_50d": q.get("fiftyDayAverageChangePercent"),
        }


def fetch_quotes(tickers):
    """주가·시가총액·등락률을 한 번에. 종목당 개별 호출을 대신한다.

    배치가 하나라도 실패하면 그 종목들의 시가총액이 통째로 빈다(차트 API
    폴백은 시총을 못 준다). 그래서 crumb 을 새로 받아 한 번 더 시도하고,
    그래도 남으면 개별로 훑는다.
    """
    out = {}
    session, crumb = _crumb_session()
    if session and crumb:
        failed = []
        for i in range(0, len(tickers), QUOTE_BATCH):
            batch = tickers[i:i + QUOTE_BATCH]
            try:
                _quote_batch(session, crumb, batch, out)
            except Exception as e:             # noqa: BLE001
                print(f"[quote] 배치 실패({len(batch)}종목): {type(e).__name__}: {e}")
                failed.extend(batch)

        # crumb 이 만료됐을 수 있으니 새 세션으로 재시도
        retry = [t for t in failed if t not in out]
        if retry:
            session2, crumb2 = _crumb_session()
            if session2 and crumb2:
                for i in range(0, len(retry), QUOTE_BATCH):
                    try:
                        _quote_batch(session2, crumb2, retry[i:i + QUOTE_BATCH], out)
                    except Exception:          # noqa: BLE001
                        pass
                print(f"[quote] 재시도로 {len(retry) - len([t for t in retry if t not in out])}"
                      f"/{len(retry)}종목 복구")

    # 시총이 비는 종목은 개별로 한 번 더(수가 적을 때만)
    no_cap = [t for t in tickers if t in out and not out[t].get("market_cap")]
    if session and crumb and 0 < len(no_cap) <= 15:
        for t in no_cap:
            try:
                _quote_batch(session, crumb, [t], out)
            except Exception:                  # noqa: BLE001
                pass

    # crumb 자체가 막히면 차트 API 로 등락률만이라도 채운다(시총은 없음)
    missing = [t for t in tickers if t not in out]
    if missing:
        def one(sym):
            try:
                s = yahoo_series(sym, rng="5d")
                return sym, {"price": s[-1][1], "market_cap": None,
                             "chg_pct": pct_change(s)}
            except Exception:                  # noqa: BLE001
                return sym, {"price": None, "market_cap": None, "chg_pct": None}
        with ThreadPoolExecutor(max_workers=12) as ex:
            out.update(dict(ex.map(one, missing)))
        print(f"[quote] {len(missing)}종목은 차트 API 로 대체(시가총액 없음)")
    return out


RETURN_WINDOWS = (("m1", 30), ("m3", 91), ("m6", 182), ("m12", 365))


def _return_at(series, days_back: int):
    """days_back 일 전 종가 대비 등락률(%). 그 날짜에 가장 가까운 거래일을 쓴다."""
    if len(series) < 2:
        return None
    target = series[-1][0] - dt.timedelta(days=days_back)
    past = [(d, v) for d, v in series if d <= target]
    if not past:                               # 상장 기간이 짧으면 구간을 못 만든다
        return None
    base = past[-1][1]
    return (series[-1][1] / base - 1.0) * 100.0 if base else None


def fetch_returns(tickers):
    """종목별 1개월·6개월·12개월 등락률 + 2개년 시계열(OHLC 포함).

    같은 차트 API 가 시가·고가·저가·종가를 한 번에 주므로 종가만 쓰고 버리지
    않는다. 웹 대시보드의 종목 상세는 이 OHLC 로 캔들차트를 그린다(5튜플:
    date, open, high, low, close).
    반환: {sym: {"returns": {...}, "series": [...], "ohlc": [...]}}.
    """
    def one(sym):
        try:
            # 1y 로 받으면 365일 전 시점이 구간 밖이라 12M 수익률이 비므로 2y 로 받는다
            o = yahoo_candles(sym, rng="2y")
        except Exception:                      # noqa: BLE001
            return sym, {"returns": {}, "series": [], "ohlc": []}
        s = [(d, c) for d, _, _, _, c in o]
        return sym, {"returns": {k: _return_at(s, d) for k, d in RETURN_WINDOWS},
                     "series": s, "ohlc": o}

    with ThreadPoolExecutor(max_workers=12) as ex:
        return dict(ex.map(one, tickers))


def fetch_sector_holdings(sector_symbols, caps=None):
    """섹터별 시총 상위 종목 + 각 종목의 주가·시가총액·당일 등락률·2개년 시계열.

    WEB_TOP_N 개까지 담는다. 텔레그램 캡션은 그중 앞 TOP_N 개만 쓰고,
    웹 대시보드는 전부 보여준다(화면은 길이 제약이 없다).

    caps 는 나스닥 스크리너에서 받은 {티커: 시가총액}. 시총 순위를 여기서
    매기는 이유는 두 가지 실측 때문이다.
      - 야후 시세는 시총이 통째로 비는 종목이 있다. AMD 는 XLK 시총 6위인데
        시세에 시총이 없어 0 으로 밀려 표에서 사라졌다(CRM 도 같았다).
      - 후보를 ETF 비중 상위 25 로 자르면 비중은 낮고 시총은 큰 종목이
        후보에조차 못 든다(MU 가 그랬다). 그래서 보유목록 전량을 후보로 본다.
    """
    etfs = [s for s in sector_symbols if s not in CURATED]
    with ThreadPoolExecutor(max_workers=6) as ex:
        holdings = dict(zip(etfs, ex.map(lambda e: _top_holdings(e, n=999), etfs)))
    # 큐레이션 섹터(원자력)는 보유목록 공시가 없어 목록을 직접 넣는다
    for sym in sector_symbols:
        if sym in CURATED:
            holdings[sym] = [{"ticker": t, "name": n}
                             for t, n in CURATED[sym].items()]

    caps = dict(caps or {})
    tickers = sorted({h["ticker"] for hs in holdings.values() for h in hs})
    # 스크리너에 없는 종목만 시세로 시총을 확인한다(전량에 시세를 돌리면 비싸다)
    unknown = [t for t in tickers if not caps.get(t)]
    if unknown:
        for t, q in fetch_quotes(unknown).items():
            if q.get("market_cap"):
                caps[t] = q["market_cap"]

    for sym, hs in list(holdings.items()):
        hs.sort(key=lambda h: caps.get(h["ticker"]) or 0, reverse=True)
        holdings[sym] = hs[:WEB_TOP_N]

    # 살아남은 종목만 시세를 받는다(주가·당일 등락률)
    kept = sorted({h["ticker"] for hs in holdings.values() for h in hs})
    quotes = fetch_quotes(kept)
    for hs in holdings.values():
        for h in hs:
            h.update({k: v for k, v in (quotes.get(h["ticker"]) or {}).items()
                      if v is not None})
            if not h.get("market_cap"):
                h["market_cap"] = caps.get(h["ticker"])

    rets = fetch_returns(kept)
    for hs in holdings.values():
        for h in hs:
            r = rets.get(h["ticker"]) or {}
            h["returns"] = r.get("returns", {})
            h["series"] = r.get("series", [])
            h["ohlc"] = r.get("ohlc", [])
    return holdings


# ---------------------------------------------------------- 주도주 슬롯
# 섹터당 시총 상위 5만 보면 최근 주가가 크게 오른 종목이 구조적으로 빠진다.
# 실측(2026-08-03): 팔란티어는 XLK 안에 있지만 섹터 내 시총 12/76위라 잘리고,
# 블룸에너지(시총 64B)는 S&P 500·400·600 어디에도 없어 SPDR 보유목록 자체에
# 등장하지 않는다. 그래서 후보를 지수가 아니라 '미국 상장 전종목'에서 만든다.
NASDAQ_SCREENER = ("https://api.nasdaq.com/api/screener/stocks"
                   "?tableonly=true&limit=25&offset=0&download=true")

# 나스닥 스크리너의 자체 섹터 분류 -> 우리가 쓰는 SPDR 섹터.
# GICS 와 완전히 같지는 않다(블룸에너지는 GICS 로 산업재인데 여기선 에너지).
# 아래 WATCHLIST 로 개별 종목을 원하는 섹터에 고정할 수 있다.
NASDAQ_SECTOR = {
    "Technology": "XLK",
    "Telecommunications": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Finance": "XLF",
    "Health Care": "XLV",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}

# 보통주가 아닌 것(워런트·유닛·우선주·예탁증서)은 이름으로 걸러낸다
NOT_COMMON = re.compile(
    r"\b(WARRANT|UNIT|PREFERRED|DEPOSITARY|RIGHTS?|NOTES?|TRUST PREFERRED)\b", re.I)

MOM_MIN_CAP = 10e9      # 주도주 후보 시가총액 하한(100억 달러) — 잡주를 막는다
MOM_MIN_VOL = 300_000   # 하루 거래량 하한 — 유동성 없는 종목은 뉴스도 없다
MOM_N = 2               # 섹터당 주도주 자리 수

# 주도주는 '지금 시장을 이끄는 종목'이다. 1년 등락률로 뽑으면 작년에 오르고
# 올해 내내 흘러내린 종목이 올라온다(실측: 팔란티어는 1년 -23%인데도 한때
# 주도주였고, 1년 +470% 인 종목이 최근 50일선 아래인 경우도 있었다).
# 그래서 3개월 등락률을 주 지표로 쓰고, 최근 1개월이 무너지지 않았는지로 거른다.
MOM_MIN_3M = 15.0       # 3개월 등락률 하한(%)
MOM_MIN_1M = 0.0        # 1개월 등락률 하한(%) — 최근에 꺾인 종목은 뺀다
# 3개월 등락률은 시세 요약(quote)에 없고 시계열을 받아야 나온다. 전 종목의
# 시계열을 받을 수는 없으니, 50일선 이격(quote 에 있다)으로 섹터당 후보를
# 이만큼 좁힌 뒤 그 후보만 실제 시계열로 재계산한다.
MOM_PRESELECT = 12

# 점수와 무관하게 항상 넣을 종목. 값은 넣을 섹터(None 이면 스크리너 분류를 따른다).
# '지금은 모멘텀이 죽었지만 계속 보고 싶은' 종목을 여기에 적는다.
WATCHLIST = {
    "PLTR": "XLK",      # 팔란티어 — XLK 시총 12위라 상위 5 규칙에 늘 잘린다
    "BE": "XLI",        # 블룸에너지 — 지수 미편입. GICS 기준 산업재(전기장비)
}


def _cell(v):
    """스크리너 표의 '64,301,210,179.00' / '$218.32' 같은 값을 수로. 없으면 0.

    이름을 _num 으로 두면 파일 아래쪽의 국채 파싱용 _num(실패 시 None 반환)에
    가려져 시가총액이 통째로 None 이 된다(실측: 정렬에서 TypeError).
    """
    try:
        return float(str(v).replace(",", "").replace("$", "").replace("%", ""))
    except (TypeError, ValueError):
        return 0.0


def fetch_market_universe():
    """미국 상장 전종목을 섹터별로 묶는다. {섹터심볼: [{ticker, name, market_cap}]}

    나스닥 스크리너가 시총·섹터·산업까지 한 번에 준다(실측 7,113종목).
    지수 편입 여부와 무관하므로 S&P 밖 종목도 여기서는 보인다.
    """
    hdr = {**UA, "Accept": "application/json", "Referer": "https://www.nasdaq.com/"}
    r = requests.get(NASDAQ_SCREENER, headers=hdr, verify=VERIFY, timeout=40)
    r.raise_for_status()
    rows = (r.json().get("data") or {}).get("rows") or []

    out: dict[str, list] = {}
    for row in rows:
        sym = (row.get("symbol") or "").strip().upper()
        name = (row.get("name") or "").strip()
        if not sym or not re.fullmatch(r"[A-Z][A-Z.]*", sym) or NOT_COMMON.search(name):
            continue
        sector = NASDAQ_SECTOR.get((row.get("sector") or "").strip())
        # 원자력은 스크리너 분류(유틸리티·산업재·에너지)를 덮어써 한곳에 모은다
        sector = CURATED_SECTOR.get(sym) or WATCHLIST.get(sym) or sector
        if not sector:
            continue
        out.setdefault(sector, []).append({
            "ticker": _yahoo_ticker(sym), "name": name,
            "market_cap": _cell(row.get("marketCap")),
            "volume": _cell(row.get("volume")),
        })
    for hs in out.values():
        hs.sort(key=lambda h: h["market_cap"], reverse=True)
    return out


def mom_score(returns):
    """주도주 점수 — 3개월 등락률. 자격 미달이면 None.

    한 달 등락률까지 함께 보는 이유는 3개월 안에서 앞에 오르고 최근에 꺾인
    종목을 걸러내기 위해서다. 자격을 못 갖추면 자리를 비운다 — 아무도 안 오른
    섹터에 억지로 '주도주'를 앉히면 그 표시 자체가 의미를 잃는다.
    """
    r3, r1 = (returns or {}).get("m3"), (returns or {}).get("m1")
    if r3 is None or r3 < MOM_MIN_3M:
        return None
    if r1 is not None and r1 < MOM_MIN_1M:
        return None
    return r3


def pick_extras(holdings, universe):
    """섹터마다 시총 상위 5 밖의 '주도주'와 워치리스트 종목을 골라 붙인다.

    반환값은 {섹터: [종목 dict]} 이고, 각 종목에는 pick 표시가 붙는다
    ('momentum' = 최근 흐름으로 뽑힘, 'watch' = 사람이 지정).
    시총 상위 5는 손대지 않는다 — 자리 고정이 이 브리핑의 기본이다.
    """
    if not universe:
        return {}
    holdings = holdings or {}
    have = {h["ticker"] for hs in holdings.values() for h in hs}

    # 시세 조회 비용을 아끼려고 시총·거래량 하한을 먼저 건다
    cands = {}
    for sector, rows in universe.items():
        keep = [r for r in rows
                if r["ticker"] not in have
                and (r["market_cap"] >= MOM_MIN_CAP or r["ticker"] in WATCHLIST)
                and (r["volume"] >= MOM_MIN_VOL or r["ticker"] in WATCHLIST)]
        if keep:
            cands[sector] = keep

    quotes = fetch_quotes(sorted({r["ticker"] for rs in cands.values() for r in rs}))

    # 1차: 50일선 위에 있는 종목만, 이격이 큰 순으로 섹터당 MOM_PRESELECT 개.
    #      전 종목의 시계열을 받는 건 불가능하므로 여기서 후보를 줄인다.
    short = {}
    for sector, rows in cands.items():
        keep = [r for r in rows
                if (quotes.get(r["ticker"]) or {}).get("vs_50d") is not None
                and quotes[r["ticker"]]["vs_50d"] > 0]
        keep.sort(key=lambda r: quotes[r["ticker"]]["vs_50d"], reverse=True)
        watch = [r for r in rows if r["ticker"] in WATCHLIST
                 and WATCHLIST[r["ticker"]] in (None, sector)]
        short[sector] = watch + [r for r in keep[:MOM_PRESELECT]
                                 if r["ticker"] not in {w["ticker"] for w in watch}]

    # 2차: 후보의 실제 시계열로 3개월·1개월 등락률을 재고 자격을 본다
    rets = fetch_returns(sorted({r["ticker"] for rs in short.values() for r in rs}))

    extras = {}
    for sector, rows in short.items():
        picked, seen = [], set()
        for r in rows:                       # 워치리스트가 먼저 자리를 잡는다
            if r["ticker"] in WATCHLIST and WATCHLIST[r["ticker"]] in (None, sector):
                picked.append({**r, "pick": "watch"})
                seen.add(r["ticker"])
        scored = [(s, r) for r in rows if r["ticker"] not in seen
                  and (s := mom_score((rets.get(r["ticker"]) or {})
                                      .get("returns"))) is not None]
        scored.sort(key=lambda sr: sr[0], reverse=True)
        for _, r in scored[:MOM_N]:
            picked.append({**r, "pick": "momentum"})
        for p in picked:
            r = rets.get(p["ticker"]) or {}
            # 시세가 비는 값(None)으로 스크리너 값을 덮지 않는다. 덮으면 시총이
            # 통째로 사라진다(실측: SharkNinja·Hormel 등이 0B 로 찍혔다).
            p.update({k: v for k, v in (quotes.get(p["ticker"]) or {}).items()
                      if v is not None})
            p["returns"] = r.get("returns", {})
            p["series"] = r.get("series", [])
            p["ohlc"] = r.get("ohlc", [])
        if picked:
            extras[sector] = picked
    return extras


# ------------------------------------------------ 기업 개요·분기 실적
QUOTE_SUMMARY = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{sym}"
FUNDAMENTALS = ("https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/"
                "finance/timeseries/{sym}")
# 실측(2026-08-03): quoteSummary 의 분기 손익은 4분기까지만 오고 영업이익이 비지만,
# fundamentals-timeseries 는 5분기치를 영업이익까지 채워서 준다.
FUND_TYPES = ",".join("quarterly" + t for t in
                      ("TotalRevenue", "OperatingIncome", "NetIncome", "BasicEPS"))
QUARTERS = 5
SUMMARY_MAX = 400        # 개요는 화면에서 읽을 만큼만 — 야후 원문은 2천 자에 이른다


def _fund_series(rows, key):
    out = {}
    for row in rows:
        for v in row.get(key) or []:
            if v and v.get("asOfDate") is not None:
                out[v["asOfDate"]] = (v.get("reportedValue") or {}).get("raw")
    return out


def _trim(text, limit=SUMMARY_MAX):
    """문장 중간에서 자르지 않는다 — 마지막 마침표까지만 남긴다."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    dot = max(cut.rfind(". "), cut.rfind("다. "), cut.rfind("함. "))
    return (cut[:dot + 1] if dot > limit * 0.5 else cut.rstrip() + "…")


def fetch_company_info(tickers):
    """{티커: {"profile": {...}, "quarters": [...]}} — 기업 개요와 최근 5개 분기 실적.

    개요는 야후 assetProfile(영문), 실적은 fundamentals-timeseries 를 쓴다.
    종목 하나가 실패해도 그 종목만 비고 나머지는 그대로 나간다.
    """
    session, crumb = _crumb_session()
    if not (session and crumb):
        print("[info] crumb 실패 — 기업 개요·실적 생략")
        return {}
    # 5분기를 받으려면 2년 남짓 거슬러 올라가야 한다(분기 결산일 기준)
    p2 = int(dt.datetime.now().timestamp())
    p1 = p2 - int(60 * 60 * 24 * 365 * 2.2)

    def one(sym):
        info = {}
        try:
            r = session.get(QUOTE_SUMMARY.format(sym=sym),
                            params={"modules": "assetProfile", "crumb": crumb},
                            timeout=TIMEOUT)
            p = ((r.json().get("quoteSummary", {}).get("result") or [{}])[0]
                 .get("assetProfile") or {})
            if p:
                info["profile"] = {
                    "summary": _trim(p.get("longBusinessSummary")),
                    "industry": p.get("industry") or "",
                    "employees": p.get("fullTimeEmployees"),
                    "website": p.get("website") or "",
                }
        except Exception:                          # noqa: BLE001
            pass
        try:
            r = session.get(FUNDAMENTALS.format(sym=sym),
                            params={"symbol": sym, "type": FUND_TYPES,
                                    "merge": "false", "period1": p1, "period2": p2,
                                    "crumb": crumb}, timeout=TIMEOUT)
            rows = (r.json().get("timeseries") or {}).get("result") or []
            rev = _fund_series(rows, "quarterlyTotalRevenue")
            op = _fund_series(rows, "quarterlyOperatingIncome")
            net = _fund_series(rows, "quarterlyNetIncome")
            eps = _fund_series(rows, "quarterlyBasicEPS")
            dates = sorted(rev or net or op)[-QUARTERS:]
            info["quarters"] = [{"date": d, "revenue": rev.get(d),
                                 "op": op.get(d), "net": net.get(d),
                                 "eps": eps.get(d)} for d in dates]
        except Exception:                          # noqa: BLE001
            pass
        return sym, info

    with ThreadPoolExecutor(max_workers=8) as ex:
        out = dict(ex.map(one, tickers))
    got = sum(1 for v in out.values() if v.get("quarters"))
    print(f"[info] 기업 개요·실적 {got}/{len(tickers)}종목")
    return out


def attach_company_info(holdings):
    """holdings 각 종목에 profile·quarters 를 붙인다(제자리 수정)."""
    if not holdings:
        return holdings
    tickers = sorted({h["ticker"] for hs in holdings.values() for h in hs})
    info = fetch_company_info(tickers)
    for hs in holdings.values():
        for h in hs:
            i = info.get(h["ticker"]) or {}
            h["profile"] = i.get("profile") or {}
            h["quarters"] = i.get("quarters") or []
    return holdings


def fetch_kr_proxy():
    """한국증시 야간 프록시 — EWY(iShares MSCI South Korea, 미국장 거래).

    코스피200 야간선물 시세는 무료 소스가 없다(야후·네이버 모두 미제공,
    구 Eurex 연계 야간시장 종료 — 러너 프로브로 실측 확인). 대신 미국장에서
    거래되는 한국 ETF 의 등락을 야간 대용치로 쓴다. 프록시임을 표기한다.
    """
    o = yahoo_candles("EWY")
    s = [(d, c) for d, _, _, _, c in o]
    return {"series": s, "ohlc": o, "last": s[-1][1], "chg_pct": pct_change(s),
            "returns": {k: _return_at(s, d) for k, d in RETURN_WINDOWS}}


def sort_sectors_by_cap(sectors, holdings):
    """섹터를 시가총액 큰 순으로 고정 정렬한다(제자리 정렬).

    등락률 순으로 두면 순서가 매일 바뀌어 어제 화면과 대조하기 어렵다.
    시총 순위는 거의 변하지 않으므로 사실상 고정된 자리표가 된다.
    섹터 전체 시총 대신 상위 보유종목 시총 합을 쓴다 — 순위는 같다.
    """
    if not sectors:
        return sectors
    holdings = holdings or {}

    def cap(s):
        # 주도주·워치리스트(pick 표시가 붙은 종목)는 자리표에서 뺀다.
        # 그것까지 더하면 그날 뽑힌 종목에 따라 섹터 순서가 다시 흔들린다.
        return sum((h.get("market_cap") or 0)
                   for h in holdings.get(s["symbol"], []) if not h.get("pick"))

    sectors.sort(key=cap, reverse=True)
    return sectors


def fetch_fx():
    o = yahoo_candles("KRW=X")
    s = [(d, c) for d, _, _, _, c in o]
    diff = s[-1][1] - s[-2][1] if len(s) > 1 else 0.0
    return {"series": s, "ohlc": o, "last": s[-1][1], "chg": diff}


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
        last = _num(q.get("last"))
        if last is None:
            continue
        # CNBC 는 변동이 작으면 change 를 아예 안 준다(2년물이 UNCH 로 와서
        # 늘 0.0bp 로 표시됐다). 그때는 전일 종가와의 차이로 직접 계산한다.
        chg = _num(q.get("change"))
        if chg is None:
            prev = _num(q.get("previous_day_closing"))
            chg = (last - prev) if prev is not None else 0.0
        out[sym] = {
            "label": TENOR_LABEL[sym],
            "yield": last,
            "chg_bp": chg * 100.0,            # %p -> bp
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

    # 전 종목 시가총액·섹터(히트맵과 같은 재료). 섹터 상위 종목을 고르는
    # 기준이므로 보유목록보다 먼저 받는다.
    run("universe", fetch_market_universe)
    caps = {r["ticker"]: r["market_cap"]
            for rows in (data.get("universe") or {}).values() for r in rows}
    run("holdings", lambda: fetch_sector_holdings([s for s, _ in SECTORS], caps))
    sort_sectors_by_cap(data.get("sectors"), data.get("holdings"))

    # 시총 상위 뒤에 주도주·워치리스트를 붙인다. 실패해도 상위 종목은 그대로 나간다.
    run("extras", lambda: pick_extras(data.get("holdings"), data.get("universe")))
    for sym, extra in (data.get("extras") or {}).items():
        if data.get("holdings") is not None and sym in data["holdings"]:
            data["holdings"][sym].extend(extra)
    data.pop("universe", None)          # 7천 종목 목록은 이후 단계에서 안 쓴다

    # 웹 대시보드에서 종목을 눌렀을 때 보여줄 기업 개요와 분기 실적.
    # 실패해도 시세·차트는 그대로 나간다.
    run("company_info", lambda: attach_company_info(data.get("holdings")))
    data.pop("company_info", None)

    run("fx", fetch_fx)
    run("kr_proxy", fetch_kr_proxy)
    run("ust_now", fetch_treasury_now)
    run("ust_hist", fetch_treasury_history)
    run("domestic", fetch_domestic)

    data["errors"] = errors
    data["asof"] = dt.datetime.now().astimezone()
    return data
