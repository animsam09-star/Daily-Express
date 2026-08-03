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

import kr_universe

from sources import (RETURN_WINDOWS, TIMEOUT, UA, VERIFY, _return_at,
                     fetch_quotes, pct_change, yahoo_candles, yahoo_ohlc,
                     yahoo_series)

if not VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

INDICES = [("^KS11", "코스피"), ("^KQ11", "코스닥")]

POOL_TOP = 20        # 테마별로 지수에 넣을 종목 수(시가총액 상위순)

_POOLS = None


def get_pools():
    """테마 -> 종목코드. 업종 분류와 테마 ETF 에서 만든다(kr_universe)."""
    global _POOLS
    if _POOLS is None:
        try:
            _POOLS = kr_universe.build_pools()
        except Exception as e:              # noqa: BLE001
            print(f"[kr] 테마 구성 실패, 최소 구성으로 진행: {type(e).__name__}: {e}")
            _POOLS = {"반도체": list(kr_universe.SEMI_LARGE)}
    return _POOLS

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
    pools = get_pools()
    codes = sorted({c for pool in pools.values() for c in pool})
    quotes = _resolve_suffixes(codes)          # 코스피/코스닥 접미사 확정

    # 테마마다 시가총액 상위 POOL_TOP 개만 쓴다. 금액 하한으로 자르면
    # 테마별 종목 수가 들쭉날쭉해져 지수 간 비교가 어렵다.
    def cap(c):
        return (quotes.get(_ys(c)) or {}).get("market_cap") or 0

    def weight(theme, c):
        """지수 가중치. ETF 로 정의된 테마는 ETF 구성비중, 나머지는 시가총액."""
        w = kr_universe.ETF_WEIGHTS.get(theme)
        return w.get(c, 0.0) if w else cap(c)

    pools = {t: sorted([c for c in cs if cap(c) > 0],
                       key=lambda c: weight(t, c), reverse=True)[:POOL_TOP]
             for t, cs in pools.items()}
    pools = {t: cs for t, cs in pools.items() if cs}

    hist_codes = {c for cs in pools.values() for c in cs}
    name_codes = {c for cs in pools.values() for c in cs[:TOP_N]}

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_names = ex.submit(fetch_names, sorted(name_codes))
        f_rets = ex.submit(_fetch_returns, [_ys(c) for c in sorted(hist_codes)])
    names, rets = f_names.result(), f_rets.result()

    sectors, holdings = [], {}
    for theme, pool in pools.items():
        rows = []
        for c in pool:
            q = quotes.get(_ys(c)) or {}
            if q.get("price") is None:
                continue
            r = rets.get(_ys(c)) or {}
            rows.append({"ticker": c, "name": names.get(c, c),
                         "price": q.get("price"), "market_cap": q.get("market_cap"),
                         "chg_pct": q.get("chg_pct"),
                         "returns": r.get("returns", {}),
                         "series": r.get("series", []),
                         "ohlc": r.get("ohlc", [])})
        if not rows:
            continue
        for r in rows:
            r["weight"] = weight(theme, r["ticker"])
        # 지수 가중치와 같은 순서로 보여준다(ETF 테마는 구성비중, 그 외 시총)
        rows.sort(key=lambda r: r["weight"], reverse=True)
        holdings[theme] = rows[:TOP_N]         # 메시지에는 상위 5종목만

        # 섹터 지표·차트는 테마 전체(후보 풀)를 시총가중해 만든다.
        # 상위 5개만 쓰면 소부장(12종목)처럼 저변이 넓은 테마의 대표성이 떨어진다.
        wsum = sum(r["weight"] for r in rows)
        chg = (sum((r.get("chg_pct") or 0) * r["weight"] for r in rows) / wsum
               if wsum else 0.0)
        sec_ret = {}
        for k, _ in RETURN_WINDOWS:
            vals = [(r["returns"].get(k), r["weight"]) for r in rows
                    if (r.get("returns") or {}).get(k) is not None]
            if vals:
                tw = sum(w for _, w in vals)
                sec_ret[k] = sum(v * w for v, w in vals) / tw if tw else None
        sectors.append({"symbol": theme, "name": theme, "chg_pct": chg,
                        "returns": sec_ret, "series": None,
                        "members": rows, "member_count": len(rows),
                        "cap_sum": sum((r.get("market_cap") or 0) for r in rows)})

    # 시가총액 큰 순으로 고정. 등락률 순으로 두면 순서가 매일 바뀌어
    # 어제 화면과 대조하기 어렵다(반도체 → 전기전자 → … 로 자리가 고정된다).
    sectors.sort(key=lambda s: s.get("cap_sum") or 0, reverse=True)
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
    """{sym: {"returns", "series", "ohlc"}}. 웹 대시보드 종목 상세는 이
    OHLC 로 캔들차트를 그린다 — 수익률 계산에 어차피 받는 것을 버리지 않는다."""
    def one(sym):
        try:
            o = yahoo_candles(sym, rng="2y")
        except Exception:                      # noqa: BLE001
            return sym, {"returns": {}, "series": [], "ohlc": []}
        s = [(d, c) for d, _, _, _, c in o]
        return sym, {"returns": {k: _return_at(s, d) for k, d in RETURN_WINDOWS},
                     "series": s, "ohlc": o}

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
    # _fetch_returns 가 이미 받아둔 시계열을 재사용하고, 없을 때만 다시 받는다
    with ThreadPoolExecutor(max_workers=5) as ex:
        fetched = ex.map(
            lambda x: x.get("series") or _safe_series(_ys(x["ticker"])), rows)
        for r, s in zip(rows, fetched):
            if s:
                series_list.append((r.get("weight") or r.get("market_cap") or 1, s))
    if not series_list:
        return []

    # 지수는 공통 거래일 교집합으로 만드는데, 상장 2년 미만 종목이 하나라도
    # 있으면 차트 전체가 그 종목의 상장일부터로 잘린다(조선·기계가
    # 2024년 상장한 HD현대마린솔루션 때문에 1년 남짓만 그려졌다).
    # 창 시작보다 90일 넘게 늦게 시작하는 종목은 지수에서 뺀다.
    window_start = min(s[0][0] for _, s in series_list)
    kept = [(w, s) for w, s in series_list
            if (s[0][0] - window_start).days <= 90]
    if len(kept) < len(series_list):
        print(f"[kr] {theme}: 시계열 짧은 {len(series_list) - len(kept)}종목은 "
              f"지수에서 제외(차트 창 보존)")
    series_list = kept or series_list

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


# 시황 문장에 쓰는 기간 누적 창. 달력일 기준(3M=91일 등)으로 자른다.
FLOW_WINDOWS = (("3M", 91), ("6M", 182), ("12M", 365), ("24M", 730))


def fetch_flows():
    """당일·YTD·기간별(3M/6M/12M/24M) 누적 순매수(억원)와 외국인 24개월
    누적 시계열. 개인·외국인·기관.

    네이버는 한 페이지에 약 20 영업일만 보여주므로 bizdate 를 거슬러 올리며
    24개월 전까지 모은다. 시황 문장의 기간 누적과 외국인 누적 차트가
    모두 이 데이터에서 나온다.
    """
    today = dt.date.today()
    start = today - dt.timedelta(days=FLOW_WINDOWS[-1][1])
    jan1 = dt.date(today.year, 1, 1)
    out = {}
    for sosok, label in FLOW_MARKETS:
        seen, cursor = {}, today
        for _ in range(40):                     # 40페이지면 800 영업일, 24개월이면 충분
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
            if oldest <= start:
                break
            cursor = oldest - dt.timedelta(days=1)

        if not seen:
            continue
        latest = max(seen)
        coverage = min(seen)
        keys = ("개인", "외국인", "기관")

        def cum_since(cut):
            acc = [0.0, 0.0, 0.0]
            for d, vals in seen.items():
                if d >= cut:
                    for i in range(3):
                        acc[i] += vals[i]
            return dict(zip(keys, acc))

        # 데이터가 창의 시작까지 닿지 않으면 그 창은 None — 부분 누적을
        # 온전한 누적처럼 보여주는 것보다 빼는 편이 낫다. (7일 여유는 휴장 감안)
        windows = {}
        for k, days in FLOW_WINDOWS:
            cut = latest - dt.timedelta(days=days)
            windows[k] = (cum_since(cut)
                          if coverage <= cut + dt.timedelta(days=7) else None)

        # 외국인 24개월 누적 시계열(차트용). 오래된 날부터 누적해 나간다.
        foreign_cum, acc = [], 0.0
        for d in sorted(seen):
            if d < start:
                continue
            acc += seen[d][1]                   # [개인, 외국인, 기관] 중 외국인
            foreign_cum.append((d, acc))

        out[label] = {
            "date": latest,
            "today": dict(zip(keys, seen[latest])),
            "ytd": cum_since(jan1),
            "windows": windows,
            "foreign_cum": foreign_cum,
        }
    return out


def _safe_series(sym):
    try:
        return yahoo_series(sym, rng="2y")
    except Exception:                          # noqa: BLE001
        return []
