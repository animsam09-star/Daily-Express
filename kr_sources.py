"""한국 증시 데이터 수집.

미국판(sources.py)과 같은 구조지만 종목·섹터·통화가 다르다.
야후가 한국 종목을 `005930.KS` 형태로 지원해 시세·시가총액·히스토리를
같은 방식으로 받을 수 있다(실측 확인).

섹터는 GICS 가 아니라 국내 리서치에서 실제로 쓰는 테마로 나눈다.
네이버 업종(79개)은 GICS 세분류라 '담배'에 KT&G 한 종목만 있는 식이라 쓸 수 없고,
GICS 11개로 묶으면 반도체가 '기술'에 묻혀 국내 시장의 체감과 어긋난다.
"""
from __future__ import annotations

import datetime as dt
import re
from concurrent.futures import ThreadPoolExecutor

import requests
import urllib3

from sources import (RETURN_WINDOWS, TIMEOUT, UA, VERIFY, _return_at,
                     fetch_quotes, pct_change, yahoo_ohlc, yahoo_series)

if not VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

INDICES = [("^KS11", "코스피"), ("^KQ11", "코스닥")]

# 테마별 후보 풀. 이 중 시가총액 상위 5종목을 매일 자동으로 고른다.
# 풀을 넓게 두면 순위가 바뀌어도 코드를 고칠 필요가 없다.
SECTOR_POOL = {
    "반도체": ["005930", "000660", "000990", "402340", "222800"],
    "반도체 소부장": ["042700", "403870", "058470", "095340", "240810", "036930",
                 "357780", "005290", "064760", "101490", "319660", "084370"],
    "2차전지": ["373220", "006400", "003670", "247540", "066970", "137400", "020150"],
    "자동차": ["005380", "000270", "012330", "204320", "018880", "011210"],
    "바이오·제약": ["207940", "068270", "000100", "128940", "326030", "302440", "196170"],
    "인터넷·게임": ["035420", "035720", "259960", "036570", "251270", "263750", "112040"],
    "금융": ["105560", "055550", "086790", "032830", "138040", "316140", "071050"],
    "화학·소재": ["051910", "011170", "011780", "298020", "011790", "005490", "004020"],
    "조선·기계": ["009540", "042660", "010140", "329180", "034020", "267250"],
    "방산·항공": ["012450", "079550", "064350", "272210", "003490"],
    "우주": ["047810", "099320", "451760", "462350", "211270", "189300"],
    "로봇": ["454910", "277810", "108490", "348340", "388720", "117730", "058610"],
    "신재생": ["009830", "010060", "112610", "336260", "322000", "288620",
             "018000", "475150", "100090"],
    "화장품": ["090430", "278470", "051900", "192820", "161890", "002790",
             "257720", "237880", "241710"],
    "의류·유통": ["139480", "004170", "069960", "023530", "383220", "020000",
               "111770", "081660", "282330", "007070"],
}

TOP_N = 5
NAVER_HDR = {**UA, "Referer": "https://finance.naver.com/"}
NAME_URL = "https://finance.naver.com/item/main.naver?code={code}"
# 제목은 '삼성전자 : Npay 증권' 형태. 네이버가 표기를 바꾼 적이 있어
# 콜론 앞부분만 취한다(':' 뒤 문구에 의존하지 않는다).
NAME_RE = re.compile(r"<title>\s*([^<:]+?)\s*:", re.I)


_SUFFIX_CACHE: dict[str, str] = {}


def _ys(code: str) -> str:
    """종목코드를 야후 심볼로.

    코스피는 .KS, 코스닥은 .KQ 다. 접미사가 틀리면 시가총액이 통째로 비므로
    (에코프로비엠·알테오젠 등 코스닥 종목이 0으로 잡혀 순위가 어긋났다)
    한 번 확인한 결과를 캐시해 쓴다.
    """
    return f"{code}{_SUFFIX_CACHE.get(code, '.KS')}"


def _resolve_suffixes(codes):
    """.KS 로 시가총액이 안 나오는 종목은 .KQ 로 다시 확인해 캐시에 남긴다."""
    first = fetch_quotes([f"{c}.KS" for c in codes])
    unknown = [c for c in codes if not (first.get(f"{c}.KS") or {}).get("market_cap")]
    if not unknown:
        return first
    second = fetch_quotes([f"{c}.KQ" for c in unknown])
    for c in unknown:
        if (second.get(f"{c}.KQ") or {}).get("market_cap"):
            _SUFFIX_CACHE[c] = ".KQ"
    merged = dict(first)
    merged.update(second)
    return merged


def stock_name(code: str) -> str:
    """네이버 종목 페이지 제목에서 한글 사명."""
    try:
        r = requests.get(NAME_URL.format(code=code), headers=NAVER_HDR,
                         verify=VERIFY, timeout=TIMEOUT)
        # 네이버는 페이지마다 인코딩이 다르다(이 페이지는 UTF-8 로 바뀌었다)
        for enc in ("utf-8", "euc-kr"):
            try:
                m = NAME_RE.search(r.content.decode(enc))
            except UnicodeDecodeError:
                continue
            if m:
                return m.group(1).strip()
    except Exception:                          # noqa: BLE001
        pass
    return code


def fetch_names(codes):
    with ThreadPoolExecutor(max_workers=8) as ex:
        return dict(zip(codes, ex.map(stock_name, codes)))


def fetch_indices():
    out = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {ex.submit(yahoo_ohlc, sym): name for sym, name in INDICES}
        for f, name in futs.items():
            try:
                o = f.result()
            except Exception as e:             # noqa: BLE001
                print(f"[kr] 지수 {name} 실패: {type(e).__name__}")
                continue
            s = [(d, c) for d, _, _, c in o]
            out[name] = {"series": s, "ohlc": o, "last": s[-1][1],
                         "chg_pct": pct_change(s),
                         "returns": {k: _return_at(s, d) for k, d in RETURN_WINDOWS}}
    return out


def fetch_sectors_and_holdings():
    """테마별 시가총액 상위 5종목과, 그 시총 가중 등락률로 만든 섹터 지표.

    한국은 미국의 SPDR 처럼 테마별 ETF 보유종목 공시가 일관되지 않아,
    후보 풀에서 시총 상위를 골라 직접 집계한다.
    """
    codes = sorted({c for pool in SECTOR_POOL.values() for c in pool})
    quotes = _resolve_suffixes(codes)          # 코스피/코스닥 접미사 확정
    symbols = [_ys(c) for c in codes]

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_names = ex.submit(fetch_names, codes)
        f_rets = ex.submit(_fetch_returns, symbols)
    names, rets = f_names.result(), f_rets.result()

    sectors, holdings = [], {}
    for theme, pool in SECTOR_POOL.items():
        rows = []
        for c in pool:
            q = quotes.get(_ys(c)) or {}
            if q.get("price") is None:
                continue
            rows.append({"ticker": c, "name": names.get(c, c),
                         "price": q.get("price"), "market_cap": q.get("market_cap"),
                         "chg_pct": q.get("chg_pct"),
                         "returns": rets.get(_ys(c), {})})
        rows.sort(key=lambda r: r.get("market_cap") or 0, reverse=True)
        if not rows:
            continue
        holdings[theme] = rows[:TOP_N]         # 메시지에는 상위 5종목만

        # 섹터 지표·차트는 테마 전체(후보 풀)를 시총가중해 만든다.
        # 상위 5개만 쓰면 소부장(12종목)처럼 저변이 넓은 테마의 대표성이 떨어진다.
        wsum = sum(r.get("market_cap") or 0 for r in rows)
        chg = (sum((r.get("chg_pct") or 0) * (r.get("market_cap") or 0) for r in rows) / wsum
               if wsum else 0.0)
        sec_ret = {}
        for k, _ in RETURN_WINDOWS:
            vals = [(r["returns"].get(k), r.get("market_cap") or 0) for r in rows
                    if (r.get("returns") or {}).get(k) is not None]
            if vals:
                tw = sum(w for _, w in vals)
                sec_ret[k] = sum(v * w for v, w in vals) / tw if tw else None
        sectors.append({"symbol": theme, "name": theme, "chg_pct": chg,
                        "returns": sec_ret, "series": None,
                        "members": rows, "member_count": len(rows)})

    sectors.sort(key=lambda s: s["chg_pct"], reverse=True)
    return sectors, holdings


def attach_benchmarks(sectors, holdings, indices):
    """종목마다 자기가 속한 시장 지수를 기준으로 붙인다.

    코스닥 종목을 코스피와 비교하면 상대수익률이 왜곡된다(두 지수의 등락 폭이
    다르다). 섹터 헤더는 구성종목의 시총 비중대로 두 지수를 섞어 쓴다 —
    반도체 소부장·로봇처럼 코스닥이 주류인 테마가 있기 때문이다.
    """
    kospi = (indices.get("코스피") or {}).get("returns") or {}
    kosdaq = (indices.get("코스닥") or {}).get("returns") or {}
    if not kospi:
        return

    for theme, rows in holdings.items():
        for h in rows:
            h["bench"] = kosdaq if _SUFFIX_CACHE.get(h["ticker"]) == ".KQ" else kospi

        kq_w = sum((h.get("market_cap") or 0) for h in rows if h.get("bench") is kosdaq)
        total = sum((h.get("market_cap") or 0) for h in rows) or 1
        ratio = kq_w / total
        blended = {}
        for k in set(kospi) | set(kosdaq):
            a, b = kospi.get(k), kosdaq.get(k)
            if a is None or b is None:
                blended[k] = a if b is None else b
            else:
                blended[k] = a * (1 - ratio) + b * ratio
        for s in sectors:
            if s["symbol"] == theme:
                s["bench"] = blended


def _fetch_returns(symbols):
    def one(sym):
        try:
            s = yahoo_series(sym, rng="2y")
        except Exception:                      # noqa: BLE001
            return sym, {}
        return sym, {k: _return_at(s, d) for k, d in RETURN_WINDOWS}

    with ThreadPoolExecutor(max_workers=10) as ex:
        return dict(ex.map(one, symbols))


def sector_series(theme: str, members) -> list:
    """섹터 차트용 2개년 시계열. 테마 전체를 시총 가중해 지수처럼 만든다.

    한국은 미국의 SPDR 같은 테마 ETF 가 일관되게 없어 직접 만든다.
    가중치는 오늘의 시가총액을 2년 내내 고정 적용한 근사치다(실제 지수는
    정기적으로 재조정한다). 구간 시작을 100 으로 놓는다.
    """
    rows = members if isinstance(members, list) else (members.get(theme) or [])
    series_list = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        for r, s in zip(rows, ex.map(lambda x: _safe_series(_ys(x["ticker"])), rows)):
            if s:
                series_list.append((r.get("market_cap") or 1, s))
    if not series_list:
        return []

    common = set(d for _, s in series_list for d, _ in s)
    for _, s in series_list:
        common &= {d for d, _ in s}
    if not common:
        return []
    dates = sorted(common)
    base = {}
    for w, s in series_list:
        m = dict(s)
        base[id(s)] = m[dates[0]]
    out = []
    tw = sum(w for w, _ in series_list)
    for d in dates:
        v = sum(w * (dict(s)[d] / base[id(s)]) for w, s in series_list) / tw
        out.append((d, v * 100.0))
    return out


FLOW_URL = ("https://finance.naver.com/sise/investorDealTrendDay.naver"
            "?bizdate={d}&sosok={sosok}")
FLOW_MARKETS = (("01", "코스피"), ("02", "코스닥"))
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def _num(s):
    s = s.replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _flow_page(sosok: str, bizdate: str):
    """한 페이지(약 20 영업일)의 [날짜, 개인, 외국인, 기관] 목록."""
    r = requests.get(FLOW_URL.format(d=bizdate, sosok=sosok),
                     headers=NAVER_HDR, verify=VERIFY, timeout=TIMEOUT)
    for enc in ("euc-kr", "utf-8"):
        try:
            text = r.content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return []

    out = []
    for raw in ROW_RE.findall(text):
        cells = [re.sub(r"<[^>]+>", "", c).strip().replace("\xa0", "")
                 for c in CELL_RE.findall(raw)]
        cells = [c for c in cells if c]
        if len(cells) < 4 or not re.match(r"\d\d\.\d\d\.\d\d$", cells[0]):
            continue
        y, m, d = (int(x) for x in cells[0].split("."))
        vals = [_num(c) for c in cells[1:4]]        # 개인, 외국인, 기관
        if any(v is None for v in vals):
            continue
        out.append((dt.date(2000 + y, m, d), vals))
    return out


def fetch_flows():
    """당일과 연초 이후 누적 순매수(억원). 개인·외국인·기관.

    네이버는 한 페이지에 약 20 영업일만 보여주므로 bizdate 를 거슬러 올리며
    연초까지 모은다. 시황 문장에 '외국인 -4.9조(YTD -12.3조)' 형태로 쓴다.
    """
    today = dt.date.today()
    jan1 = dt.date(today.year, 1, 1)
    out = {}
    for sosok, label in FLOW_MARKETS:
        seen, cursor = {}, today
        for _ in range(20):                     # 20페이지면 400 영업일, 연초까지 충분
            try:
                page = _flow_page(sosok, cursor.strftime("%Y%m%d"))
            except Exception as e:              # noqa: BLE001
                print(f"[kr] 수급 {label} 실패: {type(e).__name__}")
                break
            if not page:
                break
            for d, vals in page:
                seen.setdefault(d, vals)
            oldest = min(d for d, _ in page)
            if oldest <= jan1:
                break
            cursor = oldest - dt.timedelta(days=1)

        if not seen:
            continue
        latest = max(seen)
        ytd = [0.0, 0.0, 0.0]
        for d, vals in seen.items():
            if d >= jan1:
                for i in range(3):
                    ytd[i] += vals[i]
        keys = ("개인", "외국인", "기관")
        out[label] = {
            "date": latest,
            "today": dict(zip(keys, seen[latest])),
            "ytd": dict(zip(keys, ytd)),
        }
    return out


def _safe_series(sym):
    try:
        return yahoo_series(sym, rng="2y")
    except Exception:                          # noqa: BLE001
        return []
