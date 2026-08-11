"""섹터 상위 종목의 관련 뉴스 수집 + Claude 한국어 요약 + 링크 축약.

원칙: 근거는 수집한 헤드라인과 시장 지표뿐이고, 그 안에서는 인과를 직접 서술한다.
헤드라인이 원인을 담고 있으면 "정제마진 악화로 하향" 처럼 적고, 헤드라인에 없는
원인을 지어내는 것만 금지한다. 설명이 안 되면 비워 둔다. 요약마다 근거가 된
기사 링크를 함께 붙여 사용자가 직접 확인할 수 있게 한다.

종목 메모 외에, 섹터 컨텍스트(구성 종목 등락·시장 지표)를 주면 "왜 이 섹터가
올랐/내렸나"를 1~2문장으로 종합한 섹터 코멘트도 함께 만든다.

인증이 없으면 요약을 건너뛴다(발송은 계속된다).
- CLAUDE_CODE_OAUTH_TOKEN: 구독(Pro/Max)으로 동작. 추가 과금 없음
- ANTHROPIC_API_KEY: 종량 과금
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import requests
import urllib3

from sources import TIMEOUT, UA, VERIFY

if not VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADLINES_PER_TICKER = 6   # 두 소스를 합치므로 후보를 넉넉히 넘긴다
MODEL = "claude-opus-5"
CLI_TIMEOUT = 300           # Claude Code 헤드리스 호출 상한(초)
MIN_IMPORTANCE = 3          # 이 미만은 보내지 않는다(단일 매체 소식·인사 등)

# 지정 소스에서만 기사를 찾는다. 구글 뉴스 RSS 의 site: 필터를 쓰면
# 세 곳을 한 번의 요청으로 훑을 수 있다(로이터는 직접 접근 시 401 이라 이 경로가 필요).
NEWS_SITES = ("reuters.com", "investing.com", "seekingalpha.com")
GNEWS_URL = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
NEWS_WINDOW = "when:3d"     # 최근 3일치만

YAHOO_URL = (                # 지정 소스에서 못 찾았을 때의 폴백
    "https://query1.finance.yahoo.com/v1/finance/search"
    "?q={q}&newsCount={n}&quotesCount=0"
)
TINYURL = "https://tinyurl.com/api-create.php"

ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
LINK_RE = re.compile(r"<link>(.*?)</link>", re.S)
SOURCE_RE = re.compile(r"<source[^>]*>(.*?)</source>", re.S)


def _cdata(s: str) -> str:
    return re.sub(r"<!\[CDATA\[|\]\]>", "", s).strip()


# 기사가 아니라 시세·재무 데이터 페이지. 이유를 담을 수 없으니 아예 뺀다.
DATA_PAGE_RE = re.compile(
    r"(stock price history|stock price, quote|price, quote|dividend summary"
    r"|compare against competitors|analysis & opinion|analysis &amp; opinion"
    r"|news today|quote & chart|earnings call transcript|financial statements"
    r"|balance sheet|income statement|revenue$|\(.*\) revenue$)", re.I)

# "왜"가 담긴 헤드라인의 표지. 이런 연결어가 있으면 원인 설명일 가능성이 높다.
CAUSAL_RE = re.compile(
    r"\b(after|amid|as|on|following|due to|over|because|boosted by|driven by"
    r"|beats?|misses?|announces?|unveils?|wins?|loses?|cuts?|raises?|lifts?"
    r"|upgrades?|downgrades?|warns?|guides?|approves?|rejects?|sues?|acquires?"
    r"|launches?|recalls?|halts?|resumes?|files?|reports?|signs?|invests?)\b", re.I)

# 등락만 말하는 시세 요약 표현
MOVE_RE = re.compile(
    r"\b(clos(e|es|ed|ing)|end(s|ed)|finish(es|ed)?|rise[sn]?|rose|fall[s]?|fell"
    r"|gain[s]?|drop[s]?|slide[s]?|jump[s]?|climb[s]?|slip[s]?|surge[s]?"
    r"|plunge[s]?|tumble[s]?|higher|lower|up|down|rally|rallies)\b", re.I)


def _is_recap(title: str) -> bool:
    """이유 없이 등락만 전하는 헤드라인인가.

    '왜 올랐는지'가 필요한데 '올랐다'만 있는 기사는 쓸모가 없다. 다만
    'Nvidia leads chip stocks lower after $500B deal' 처럼 등락 표현이 있어도
    원인이 붙어 있으면 살린다.
    """
    return bool(MOVE_RE.search(title)) and not CAUSAL_RE.search(title)


# 사명에서 법인격 표기를 떼어내 검색·대조에 쓸 핵심어를 남긴다
CORP_WORDS = {"CORP", "CORPORATION", "INC", "CO", "THE", "PLC", "LTD", "LLC",
              "GROUP", "HOLDINGS", "HOLDING", "COMPANY", "CLASS", "A", "B", "&",
              "SA", "NV", "AG", "TECHNOLOGIES", "TECHNOLOGY", "INTERNATIONAL",
              # 아래는 일반 명사라 기사 제목에 우연히 걸린다("shares plunge" 등)
              "SHARES", "SHS", "COMMON", "STOCK", "ORD", "CL", "SER"}


def _keywords(ticker: str, name: str) -> list[str]:
    words = [w.strip(".,/") for w in re.split(r"[\s/]+", name.upper())]
    core = [w for w in words if w and w not in CORP_WORDS and len(w) > 2]
    # 2글자 이하 티커(V, KO 등)는 다른 단어에 우연히 걸리므로 사명으로만 대조한다
    if len(ticker) <= 2 and core:
        return core[:2]
    return [ticker.upper()] + core[:2]


def _relevant(title: str, ticker: str, keys: list[str]) -> bool:
    """제목에 티커나 사명 핵심어가 실제로 들어있는 기사만 남긴다.

    티커가 짧으면 구글이 매칭에 실패해 무관한 일반 기사를 돌려준다.
    이걸 그대로 요약에 넘기면 엉뚱한 설명이 붙는다(COP 가 'cop shortage' 에,
    KO 가 복싱 기사에 걸렸다).

    티커는 대소문자를 구분해 찾는다. 기사에서 티커는 항상 대문자로 쓰이므로
    소문자 일반 단어('cop')와 갈라낼 수 있는 유일한 단서다.
    사명 핵심어는 표기가 제각각이라 대소문자를 무시한다.
    """
    if len(ticker) > 1 and re.search(
            rf"(?<![A-Za-z0-9]){re.escape(ticker)}(?![A-Za-z0-9])", title):
        return True
    up = title.upper()
    for k in keys:
        if k != ticker and re.search(
                rf"(?<![A-Z0-9]){re.escape(k)}(?![A-Z0-9])", up):
            return True
    return False


def _gnews(ticker: str, name: str) -> list[dict]:
    """구글 뉴스 RSS 에서 지정 소스 기사만. 제목·링크·출처를 돌려준다."""
    sites = " OR ".join(f"site:{d}" for d in NEWS_SITES)
    keys = _keywords(ticker, name)
    out, seen = [], set()
    queries = [f"{ticker} ({sites}) {NEWS_WINDOW}"]
    if len(keys) > 1:
        queries.append(f'"{keys[1]}" ({sites}) {NEWS_WINDOW}')
    for q in queries:
        try:
            text = _get(GNEWS_URL.format(q=requests.utils.quote(q))).text
        except Exception:                      # noqa: BLE001
            continue
        for raw in ITEM_RE.findall(text):
            t, l = TITLE_RE.search(raw), LINK_RE.search(raw)
            if not t or not l:
                continue
            title = _cdata(t.group(1))
            # 구글이 제목 끝에 " - 매체명" 을 붙인다. 출처는 따로 쓰므로 떼어낸다.
            src = _cdata(SOURCE_RE.search(raw).group(1)) if SOURCE_RE.search(raw) else ""
            if src and title.endswith(f" - {src}"):
                title = title[: -len(src) - 3]
            if title and title not in seen and _relevant(title, ticker, keys):
                seen.add(title)
                out.append({"title": title, "url": _cdata(l.group(1)), "source": src})
        if len(out) >= HEADLINES_PER_TICKER:
            break
    return out[:HEADLINES_PER_TICKER]

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "note": {
                        "type": "string",
                        "description": "한국어 요약(60자 내외, 최대 두 문장). 헤드라인이 "
                                       "등락을 설명하지 못하면 빈 문자열.",
                    },
                    "source": {
                        "type": "integer",
                        "description": "근거로 삼은 헤드라인 번호. 없으면 -1.",
                    },
                    "importance": {
                        "type": "integer",
                        "description": "1~5. 이 뉴스가 그 종목에 얼마나 중요한가. "
                                       "메모가 비면 0.",
                    },
                },
                "required": ["ticker", "note", "source", "importance"],
                "additionalProperties": False,
            },
        },
        "sectors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "comment": {
                        "type": "string",
                        "description": "섹터 등락 종합 1~2문장(90자 내외). "
                                       "근거가 없으면 빈 문자열.",
                    },
                },
                "required": ["symbol", "comment"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items", "sectors"],
    "additionalProperties": False,
}

SYSTEM = """너는 한국 증권사 리서치 어시스턴트다. 미국 주식의 당일 등락과 번호가 매겨진
관련 뉴스 헤드라인을 받아, 종목마다 한국어 메모를 만든다.

지켜야 할 규칙:
1. 반드시 제공된 헤드라인·시장 지표에만 근거한다. 입력에 없는 사실·수치·배경을
   지어내지 않는다.
2. 헤드라인이 그 종목의 등락과 무관하거나 설명이 안 되면 note 를 빈 문자열,
   source 를 -1 로 둔다. 억지로 채우지 않는 것이 맞는 답이다.
3. 원인 서술은 헤드라인이 뒷받침하는 만큼 직접적으로 한다.
   헤드라인이 원인을 담고 있으면 "실적 가이던스 하향으로 급락" 처럼 인과로 적는다.
   금지되는 것은 입력에 없는 원인을 추론으로 만들어내는 것뿐이다.
   사건은 있는데 등락과의 연결이 확실치 않으면 "~영향으로 풀이" 같은
   완충 표현을 쓴다.
4. note 는 60자 내외. 사건만이 아니라 왜(배경·수치)까지 담을 수 있으면 두 문장까지
   허용한다. 종목명은 반복하지 말고 내용만 적는다.
5. source 에는 근거로 삼은 헤드라인의 번호를 정확히 적는다.
6. 입력에 있는 모든 종목을 빠짐없이 items 에 포함한다(메모가 비어도 항목은 넣는다).
7. 가장 중요한 규칙 — note 에는 '그날 일어난 사건'만 적는다.

   쓸 수 있는 것(사건):
   실적 발표·가이던스 제시, 계약·수주·투자 결정, 인수합병, 규제 조치·소송,
   투자의견/목표주가 변경, 신제품 출시·리콜, 공급 차질, 경영진 교체,
   유가·금리·환율 같은 외부 변수의 그날 움직임.

   쓸 수 없는 것 1 (결과만 있음): "주가 상승 마감", "3% 하락", "신고가 경신".
   무엇이 움직였는지만 말할 뿐 왜 움직였는지가 없다.

   쓸 수 없는 것 2 (상시 평가): "견조한 재무구조 부각", "밸류에이션 매력",
   "장기 성장성 주목", "배당 매력", "저평가 논쟁". 어제도 오늘도 내일도 할 수 있는
   말이라 그날의 등락을 설명하지 못한다. 애널리스트 의견이라도 '무엇을 언제
   어떻게 바꿨다'가 없으면 사건이 아니다.

   둘 중 하나에 해당하거나 판단이 서지 않으면 note 를 비우고 source 를 -1 로 둔다.
   비워 두는 것이 틀린 설명을 적는 것보다 낫다.
8. 투자의견·목표주가 변경은 '왜 바꿨는지'가 핵심이다.
   "목표주가 상향", "투자의견 하향" 만 적으면 아무것도 알려주지 못한다.
   근거(정제마진 악화, 수주 증가, 가이던스 하향 등)가 헤드라인에 있으면
   그 근거를 note 에 담고, 없으면 note 를 비우고 importance 를 2 로 둔다.
   좋음: "정제마진 악화로 BofA 투자의견 하향"
   나쁨: "BofA 투자의견 하향"
   이 원칙은 다른 사건에도 똑같이 적용된다 — 무엇이 일어났는지보다
   왜 일어났는지가 담긴 메모가 낫다.

9. importance 에 1~5 로 중요도를 매긴다. 메모가 비면 0.
   5 — 회사의 실적·사업 구조를 바꾸는 사건. 어닝 서프라이즈/쇼크, 대형 인수합병,
       핵심 사업 규제·소송 결과, 대규모 수주, 경영권 변동, 상장폐지 위험.
   4 — 실적 가이던스 수정, 주요 계약 체결, 신제품 출시, 대형 투자 결정,
       복수 매체가 동시에 다룬 사건.
   3 — 근거가 명시된 투자의견·목표주가 변경, 업황 변화가 그 종목에 미치는 영향.
   2 — 근거 없는 의견 변경, 단일 매체의 소소한 소식, 정기 공시, 인사.
   1 — 관련은 있으나 주가와 연결이 약한 내용.
   입력의 '[N개 매체 보도]' 표시는 그 종목을 여러 매체가 동시에 다뤘다는 뜻이다.
   같은 사안을 여러 곳이 보도했다면 그만큼 중요하다고 본다.

10. 입력에 '## 섹터' 블록이 있으면, 각 섹터마다 sectors 배열에
    {symbol, comment} 를 만든다.
    - comment 는 그 섹터가 그날 왜 올랐/내렸는지 1~2문장(90자 내외)으로 종합한다.
    - 근거는 그 섹터 구성 종목의 등락·헤드라인과 맨 위 [시장 지표] 뿐이다.
    - 종목을 언급할 때는 반드시 티커(한국은 6자리 종목코드)를 괄호로 붙인다.
      예: "엑손모빌(XOM)", "삼성전자(005930)".
    - 그 '## 섹터' 블록 안에 있는 종목만 댈 수 있다. 다른 섹터의 종목은
      언급하지 않는다 — 엑손모빌(XOM)은 에너지 블록에만 있으므로 산업재
      코멘트에 등장할 수 없다.
    - 등락률은 입력에 적힌 값을 그대로 쓴다. 입력에 없는 숫자는 쓰지 않는다 —
      어림하거나 부풀려서 만들어 내지 않는다.
    위 세 가지를 어긴 코멘트는 기계적으로 폐기되어 화면에 아무것도 안 나온다.
    - 여러 섹터에 걸친 공통 동인(금리·유가·환율·실적 시즌)이 보이면 그걸 앞세우고,
      한두 종목이 섹터를 끌었다면 그 종목과 이유를 지목한다.
    - 개별 종목 note 와 같은 문장을 반복하지 말고 섹터 관점에서 다시 쓴다.
    - 설명할 근거가 없으면 comment 를 빈 문자열로 둔다. 억지 설명보다 낫다.
    섹터 블록이 없는 입력이면 sectors 를 빈 배열로 둔다."""


def _get(url, **kw):
    r = requests.get(url, headers=UA, verify=VERIFY, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


def _yahoo(ticker: str, name: str) -> list[dict]:
    """폴백. 지정 소스에서 아무것도 못 찾았을 때만 쓴다(노이즈가 많다)."""
    out, seen = [], set()
    for q in (ticker, name.split()[0]):
        try:
            data = _get(YAHOO_URL.format(q=requests.utils.quote(q),
                                         n=HEADLINES_PER_TICKER)).json()
        except Exception:                      # noqa: BLE001
            continue
        for n in data.get("news", []):
            t, u = (n.get("title") or "").strip(), (n.get("link") or "").strip()
            if t and t not in seen:
                seen.add(t)
                out.append({"title": t, "url": u, "source": n.get("publisher", "")})
        if len(out) >= HEADLINES_PER_TICKER:
            break
    return out[:HEADLINES_PER_TICKER]


FINNHUB_URL = "https://finnhub.io/api/v1/company-news"
FINNHUB_DAYS = 3            # 최근 며칠치를 볼 것인가
FINNHUB_WORKERS = 5         # 무료 등급 분당 60회 제한을 넘지 않도록 낮춘다
PREFERRED = ("reuters", "investing", "seeking alpha", "seekingalpha")


def _finnhub_key():
    """시크릿 이름을 FINNHUB_API 로 두든 FINNHUB_API_KEY 로 두든 받는다."""
    for n in ("FINNHUB_API_KEY", "FINNHUB_API", "FINNHUB_TOKEN"):
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return None


def _finnhub(ticker: str) -> list[dict]:
    """Finnhub 종목 뉴스. 티커로 직접 조회하므로 검색 노이즈가 없다."""
    key = _finnhub_key()
    if not key:
        return []
    today = dt.date.today()
    params = {"symbol": ticker,
              "from": (today - dt.timedelta(days=FINNHUB_DAYS)).isoformat(),
              "to": today.isoformat(), "token": key}
    for attempt in (0, 1):                     # 429 는 한 번만 쉬었다 재시도
        try:
            r = requests.get(FINNHUB_URL, params=params, headers=UA,
                             verify=VERIFY, timeout=TIMEOUT)
            if r.status_code == 429 and attempt == 0:
                time.sleep(2)
                continue
            r.raise_for_status()
            arts = r.json()
        except Exception as e:                 # noqa: BLE001
            if attempt:
                print(f"[news] finnhub {ticker} 실패: {type(e).__name__}")
            continue
        if not isinstance(arts, list):
            return []
        # 지정 매체를 앞으로, 그다음 최신순
        arts.sort(key=lambda a: (
            0 if any(p in (a.get("source") or "").lower() for p in PREFERRED) else 1,
            -(a.get("datetime") or 0)))
        out = []
        for a in arts:
            t = (a.get("headline") or "").strip()
            if t:
                out.append({"title": t, "url": (a.get("url") or "").strip(),
                            "source": (a.get("source") or "").strip()})
            if len(out) >= HEADLINES_PER_TICKER:
                break
        return out
    return []


def _preferred(src: str) -> bool:
    return any(p in (src or "").lower() for p in PREFERRED)


def _rank(arts: list[dict]) -> list[dict]:
    """데이터 페이지는 버리고, 원인이 담긴 지정 매체 기사를 앞으로 보낸다."""
    kept = [a for a in arts if not DATA_PAGE_RE.search(a["title"])]
    kept.sort(key=lambda a: (1 if _is_recap(a["title"]) else 0,
                             0 if _preferred(a.get("source")) else 1))
    return kept[:HEADLINES_PER_TICKER]


def _headlines(ticker: str, name: str) -> list[dict]:
    """Finnhub 와 지정 매체(로이터·인베스팅·시킹알파)를 둘 다 조회해 합친다.

    예전에는 Finnhub 가 성공하면 지정 매체를 아예 안 봤다. Finnhub 키가 있는
    운영 환경에서는 사실상 지정 매체가 조회되지 않았다는 뜻이다.
    """
    seen, merged = set(), []
    for group in (_gnews(ticker, name), _finnhub(ticker)):
        for a in group:
            t = a.get("title")
            if t and t not in seen:
                seen.add(t)
                merged.append(a)
    return _rank(merged or _yahoo(ticker, name))


def collect(holdings: dict) -> list[dict]:
    """섹터 상위 종목 전체에 대해 헤드라인을 붙인다(등락 폭 무관)."""
    stocks, seen = [], set()
    for hs in holdings.values():
        for h in hs:
            if h["ticker"] in seen:
                continue
            seen.add(h["ticker"])
            stocks.append({"ticker": h["ticker"], "name": h["name"],
                           "chg_pct": h.get("chg_pct")})
    if not stocks:
        return []
    # Finnhub 무료 등급은 분당 60회라 동시 요청을 낮춘다
    workers = FINNHUB_WORKERS if _finnhub_key() else 10
    with ThreadPoolExecutor(max_workers=workers) as ex:
        heads = ex.map(lambda s: _headlines(s["ticker"], s["name"]), stocks)
    for s, hl in zip(stocks, heads):
        s["headlines"] = hl
    return [s for s in stocks if s["headlines"]]


def shorten(url: str) -> str:
    """TinyURL 축약. 실패하면 원본을 그대로 쓴다."""
    if not url:
        return ""
    try:
        r = requests.get(TINYURL, params={"url": url}, headers=UA,
                         verify=VERIFY, timeout=15)
        s = r.text.strip()
        return s if r.ok and s.startswith("http") else url
    except Exception:                          # noqa: BLE001
        return url


def _stock_block(s: dict) -> list[str]:
    chg = f"{s['chg_pct']:+.2f}%" if s.get("chg_pct") is not None else "n/a"
    # 같은 사안을 여러 매체가 동시에 다뤘다면 그만큼 중요하다는 신호다.
    # 조회수는 어느 소스에서도 안 나오므로 이것이 대신할 수 있는 지표다.
    srcs = {(h.get("source") or "").split()[0].lower()
            for h in s["headlines"] if h.get("source")}
    tag = f"  [{len(srcs)}개 매체 보도]" if len(srcs) > 1 else ""
    lines = [f"[{s['ticker']}] {s['name']} {chg}{tag}"]
    for i, h in enumerate(s["headlines"]):
        src = f"  ({h['source']})" if h.get("source") else ""
        lines.append(f"  {i}. {h['title']}{src}")
    return lines


def _prompt(stocks: list[dict], sectors: list[dict] | None = None,
            holdings: dict | None = None, macro: str | None = None) -> str:
    """섹터 컨텍스트가 있으면 섹터별로 묶고, 없으면(한국판) 평면 목록으로."""
    lines = []
    if macro:
        lines += ["[시장 지표]", macro.strip(), ""]
    if not (sectors and holdings):
        for s in stocks:
            lines += _stock_block(s)
        return "\n".join(lines)

    by = {s["ticker"]: s for s in stocks}
    for sec in sectors:
        chg = f"{sec['chg_pct']:+.2f}%" if sec.get("chg_pct") is not None else "n/a"
        lines.append(f"## {sec['name']} ({sec['symbol']}) {chg}")
        members = holdings.get(sec["symbol"]) or []
        # 헤드라인이 없는 종목도 등락은 섹터 판단에 필요하다 — 한 줄로 요약해 준다.
        quiet = [h for h in members if h["ticker"] not in by]
        if quiet:
            lines.append("구성 상위(뉴스 없음): " + ", ".join(
                f"{h['ticker']} {h['chg_pct']:+.1f}%" if h.get("chg_pct") is not None
                else h["ticker"] for h in quiet))
        for h in members:
            s = by.get(h["ticker"])
            if s:
                lines += _stock_block(s)
        lines.append("")
    return "\n".join(lines)


# 코멘트는 한국어라 종목이 '엑손'처럼 음차로 나온다. 영문 사명으로 대조하면
# 걸리지 않으므로, 언어를 타지 않는 두 가지로 본다 — 괄호 안 티커와 수치.
TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,5}|\d{6})\)")
PCT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")


def _sector_facts(sectors, holdings, macro):
    """{섹터심볼: (허용 티커, 허용 수치)}. 수치는 절댓값으로 둔다.

    허용 수치는 그 섹터와 구성 종목의 등락률 + 시장 지표 블록에 적힌 값이다.
    거기에 없는 숫자가 코멘트에 나오면 지어낸 것이다.
    """
    macro_nums = {abs(float(x)) for x in PCT_RE.findall(macro or "")}
    macro_nums |= {abs(float(x)) for x in
                   re.findall(r"([+-]?\d+(?:\.\d+)?)\s*(?:bp|원|억)", macro or "")}
    out = {}
    for sec in sectors or []:
        ticks, nums = set(), set(macro_nums)
        if sec.get("chg_pct") is not None:
            nums.add(abs(float(sec["chg_pct"])))
        for h in (holdings or {}).get(sec["symbol"]) or []:
            t = (h.get("ticker") or "").strip().upper()
            if t:
                ticks.add(t)
            for k in ("chg_pct", "chg_52w", "vs_200d", "vs_50d"):
                if h.get(k) is not None:
                    nums.add(abs(float(h[k])))
            for v in (h.get("returns") or {}).values():
                if v is not None:
                    nums.add(abs(float(v)))
        out[sec["symbol"]] = (ticks, nums)
    return out


def verify_sector_notes(secs, sectors, holdings, macro=None):
    """다른 섹터의 종목을 끌어오거나 없는 수치를 지어낸 코멘트를 버린다.

    실측: 산업재(XLI) 코멘트에 '엑손이 6% 급등'이 나왔다. 엑손은 에너지(XLE)
    구성종목이고 그날 6% 오르지도 않았다(+2.12%). 모델이 옆 블록의 종목을
    가져다 수치까지 지어낸 것이다. 코멘트는 카드 제목 밑에 그대로 실리므로
    틀린 설명이 붙느니 없는 편이 낫다.
    """
    if not (secs and sectors):
        return secs
    facts = _sector_facts(sectors, holdings, macro)
    out = {}
    for sym, comment in secs.items():
        if sym not in facts:
            print(f"[news] 섹터 코멘트 버림 — 모르는 섹터 '{sym}'")
            continue
        ticks, nums = facts[sym]
        alien = {t for t in TICKER_RE.findall(comment) if t.upper() not in ticks}
        if alien:
            print(f"[news] 섹터 코멘트 버림 — {sym} 에 없는 종목: "
                  f"{', '.join(sorted(alien))}")
            continue
        # 0.2%p 는 반올림해 적은 경우를 받아주기 위한 여유다(2.12 -> 2.1).
        made_up = [v for v in (abs(float(x)) for x in PCT_RE.findall(comment))
                   if not any(abs(v - a) <= 0.2 for a in nums)]
        if made_up:
            print(f"[news] 섹터 코멘트 버림 — {sym} 입력에 없는 수치: "
                  f"{', '.join(f'{v}%' for v in made_up)}")
            continue
        out[sym] = comment
    return out


def macro_context(data: dict) -> str:
    """섹터 코멘트의 근거가 될 시장 지표 요약. 수집 실패 항목은 조용히 빠진다."""
    lines = []
    idx = data.get("indices") or {}
    parts = [f"{n} {idx[n]['chg_pct']:+.2f}%"
             for n in ("Dow", "S&P500", "Nasdaq") if idx.get(n)]
    if parts:
        lines.append("미국증시: " + ", ".join(parts))
    now = data.get("ust_now") or {}
    parts = [f"{now[s]['label']} {now[s]['yield']:.2f}%({now[s]['chg_bp']:+.1f}bp)"
             for s in ("US2Y", "US10Y", "US30Y") if now.get(s)]
    if parts:
        lines.append("미국채: " + ", ".join(parts))
    fx = data.get("fx")
    if fx and fx.get("last") is not None:
        lines.append(f"원/달러 {fx['last']:,.1f}원({fx.get('chg', 0):+.1f}원)")
    return "\n".join(lines)


def macro_context_kr(data: dict) -> str:
    """한국판 시장 지표 요약. 테마 코멘트가 기댈 근거를 만든다.

    외국인 수급을 넣는 이유는, 국내 테마 등락이 개별 뉴스보다 수급으로
    설명되는 날이 많기 때문이다.
    """
    lines = []
    idx = data.get("indices") or {}
    parts = [f"{n} {idx[n]['chg_pct']:+.2f}%"
             for n in ("코스피", "코스닥") if idx.get(n)]
    if parts:
        lines.append("국내증시: " + ", ".join(parts))
    fx = data.get("fx")
    if fx and fx.get("last") is not None:
        lines.append(f"원/달러 {fx['last']:,.1f}원({fx.get('chg', 0):+.1f}원)")
    dom = data.get("domestic") or {}
    parts = [f"{label} {dom[k]['last']:.2f}%({dom[k].get('chg', 0):+.0f}bp)"
             for k, label in (("govt_3y", "국고채 3년"), ("corp_aa3y", "회사채 AA-"))
             if (dom.get(k) or {}).get("last") is not None]
    if parts:
        lines.append("금리: " + ", ".join(parts))
    for mkt, f in (data.get("flows") or {}).items():
        today = (f or {}).get("today") or {}
        got = [f"{who} {v / 1e8:+,.0f}억" for who, v in
               (("외국인", today.get("외국인")), ("기관", today.get("기관")))
               if v is not None]
        if got:
            lines.append(f"{mkt} 수급: " + ", ".join(got))
    return "\n".join(lines)


def _json_block(text: str) -> str:
    """앞뒤 잡텍스트·코드펜스가 붙어 와도 JSON 객체만 건져낸다."""
    text = (text or "").strip()
    if text.startswith("{"):
        return text
    i, j = text.find("{"), text.rfind("}")
    return text[i:j + 1] if i != -1 and j != -1 else ""


def _parse(text: str) -> tuple[dict[str, dict], dict[str, str]]:
    """모델 출력에서 ({티커: {note, source}}, {섹터심볼: 코멘트})."""
    text = _json_block(text)
    if not text:
        return {}, {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[news] 요약 파싱 실패: {e}")
        return {}, {}
    out = {}
    for it in data.get("items", []):
        note = (it.get("note") or "").strip()
        if it.get("ticker") and note:
            try:
                imp = int(it.get("importance") or 0)
            except (TypeError, ValueError):
                imp = 0
            out[it["ticker"]] = {"note": note, "source": it.get("source", -1),
                                 "importance": imp}
    secs = {}
    raw = data.get("sectors")
    if isinstance(raw, list):
        for sc in raw:
            if not isinstance(sc, dict):
                continue
            sym = (sc.get("symbol") or "").strip()
            comment = (sc.get("comment") or "").strip()
            if sym and comment:
                secs[sym] = comment
    return out, secs


def _via_claude_code(prompt: str) -> tuple[dict, dict] | None:
    """Claude Code 헤드리스 모드. 구독 토큰으로 동작해 API 크레딧이 들지 않는다."""
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return None
    exe = shutil.which("claude")
    if not exe:
        print("[news] CLAUDE_CODE_OAUTH_TOKEN 은 있으나 claude CLI 가 없음")
        return None

    instruction = (
        SYSTEM
        + "\n\n아래 입력에 대해 JSON 만 출력한다. 설명·코드펜스 없이 JSON 객체 하나만.\n"
          '형식: {"items": [{"ticker": "TICKER", "note": "메모", "source": 0, '
          '"importance": 3}], "sectors": [{"symbol": "XLK", "comment": "섹터 코멘트"}]}\n\n'
        + prompt
    )
    try:
        r = subprocess.run([exe, "-p", instruction, "--model", MODEL],
                           capture_output=True, text=True, encoding="utf-8",
                           timeout=CLI_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[news] Claude Code 호출 실패: {type(e).__name__}: {e}")
        return None
    if r.returncode != 0:
        print(f"[news] Claude Code 오류(exit {r.returncode}): {(r.stderr or '')[:300]}")
        return None
    return _parse(r.stdout)


def _via_api(prompt: str) -> tuple[dict, dict]:
    """Anthropic API 키 경로. 사용량만큼 과금된다."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {}, {}
    try:
        import anthropic
    except ImportError:
        print("[news] anthropic 패키지 없음 — 요약 생략")
        return {}, {}
    try:
        resp = anthropic.Anthropic().beta.messages.create(
            model=MODEL, max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            system=SYSTEM,
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:                     # noqa: BLE001
        print(f"[news] 요약 호출 실패: {type(e).__name__}: {e}")
        return {}, {}
    if resp.stop_reason == "refusal":
        print("[news] 요약 거부됨 — 헤드라인만 사용")
        return {}, {}
    try:
        return _parse(next(b.text for b in resp.content if b.type == "text"))
    except StopIteration:
        return {}, {}


def build(holdings: dict, sectors: list[dict] | None = None,
          macro: str | None = None) -> tuple[dict[str, dict], dict[str, str]]:
    """({티커: {note, url}}, {섹터심볼: 코멘트}). 어느 단계가 실패해도 발송은 막지 않는다.

    sectors(각 {symbol, name, chg_pct})와 macro(시장 지표 요약)를 주면
    섹터별 등락 종합 코멘트도 함께 만든다.
    """
    try:
        stocks = collect(holdings)
    except Exception as e:                     # noqa: BLE001
        print(f"[news] 헤드라인 수집 실패: {type(e).__name__}: {e}")
        return {}, {}
    if not stocks:
        return {}, {}
    print(f"[news] {len(stocks)}종목 헤드라인 수집 — 요약 요청")

    prompt = _prompt(stocks, sectors, holdings, macro)
    result = _via_claude_code(prompt)
    if result is None:
        result = _via_api(prompt)
    summaries, sector_notes = result
    if not summaries and not sector_notes:
        return {}, {}
    sector_notes = verify_sector_notes(sector_notes, sectors, holdings, macro)

    # 요약이 나온 종목만 근거 기사 링크를 축약한다
    by_ticker = {s["ticker"]: s for s in stocks}
    targets = []
    for tk, v in summaries.items():
        src, s = v.get("source", -1), by_ticker.get(tk)
        if s and isinstance(src, int) and 0 <= src < len(s["headlines"]):
            targets.append((tk, s["headlines"][src]["url"]))

    with ThreadPoolExecutor(max_workers=8) as ex:
        short = dict(zip([t for t, _ in targets],
                         ex.map(shorten, [u for _, u in targets])))

    kept = {tk: v for tk, v in summaries.items()
            if v.get("importance", 0) >= MIN_IMPORTANCE}
    print(f"[news] 요약 {len(summaries)}건 → 중요도 {MIN_IMPORTANCE} 이상 {len(kept)}건 "
          f"/ 링크 {len(short)}건 / 섹터 코멘트 {len(sector_notes)}건")
    notes = {tk: {"note": v["note"], "url": short.get(tk, ""),
                  "importance": v.get("importance", 0)}
             for tk, v in kept.items()}
    return notes, sector_notes


# ---------------------------------------------------------------- 한국 증시
GNEWS_KR = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
KR_RECAP_RE = re.compile(
    r"(상승 ?마감|하락 ?마감|강보합|약보합|보합 ?마감|급등|급락|\d+% ?[↑↓]"
    r"|신고가|신저가|주가 ?(상승|하락|급등|급락)$)")


def _gnews_kr(name: str) -> list[dict]:
    """한국 종목 뉴스. 한국어 사명으로 찾으므로 티커 오매칭 문제가 없다."""
    out, seen = [], set()
    for q in (f'"{name}" 주가 {NEWS_WINDOW}', f'"{name}" {NEWS_WINDOW}'):
        try:
            text = _get(GNEWS_KR.format(q=requests.utils.quote(q))).text
        except Exception:                      # noqa: BLE001
            continue
        for raw in ITEM_RE.findall(text):
            t, l = TITLE_RE.search(raw), LINK_RE.search(raw)
            if not t or not l:
                continue
            title = _cdata(t.group(1))
            sm = SOURCE_RE.search(raw)
            src = _cdata(sm.group(1)) if sm else ""
            if src and title.endswith(f" - {src}"):
                title = title[: -len(src) - 3]
            # 사명이 실제로 들어간 기사만
            if title and title not in seen and name in title:
                seen.add(title)
                out.append({"title": title, "url": _cdata(l.group(1)), "source": src})
        if len(out) >= HEADLINES_PER_TICKER:
            break
    # 등락만 전하는 기사는 뒤로
    out.sort(key=lambda a: 1 if KR_RECAP_RE.search(a["title"]) else 0)
    return out[:HEADLINES_PER_TICKER]


def build_kr(holdings: dict, sectors: list[dict] | None = None,
             macro: str | None = None) -> tuple[dict[str, dict], dict[str, str]]:
    """({종목코드: {note, url}}, {테마: 코멘트}). 미국판과 같은 요약 규칙을 쓴다.

    sectors(각 {symbol, name, chg_pct})를 주면 테마별 종합 코멘트도 만든다 —
    프롬프트와 검증은 미국판과 같은 것을 쓴다(_prompt 는 시장에 무관하다).
    """
    stocks, seen = [], set()
    for hs in holdings.values():
        for h in hs:
            if h["ticker"] in seen:
                continue
            seen.add(h["ticker"])
            stocks.append({"ticker": h["ticker"], "name": h["name"],
                           "chg_pct": h.get("chg_pct")})
    if not stocks:
        return {}, {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        heads = ex.map(lambda s: _gnews_kr(s["name"]), stocks)
    for s, hl in zip(stocks, heads):
        s["headlines"] = hl
    stocks = [s for s in stocks if s["headlines"]]
    if not stocks:
        return {}, {}
    print(f"[news] 한국 {len(stocks)}종목 헤드라인 수집 — 요약 요청")

    prompt = _prompt(stocks, sectors, holdings, macro)
    result = _via_claude_code(prompt)
    if result is None:
        result = _via_api(prompt)
    summaries, sector_notes = result
    if not summaries and not sector_notes:
        return {}, {}
    sector_notes = verify_sector_notes(sector_notes, sectors, holdings, macro)

    by = {s["ticker"]: s for s in stocks}
    targets = []
    for tk, v in summaries.items():
        src, s = v.get("source", -1), by.get(tk)
        if s and isinstance(src, int) and 0 <= src < len(s["headlines"]):
            targets.append((tk, s["headlines"][src]["url"]))
    with ThreadPoolExecutor(max_workers=8) as ex:
        short = dict(zip([t for t, _ in targets],
                         ex.map(shorten, [u for _, u in targets])))
    kept = {tk: v for tk, v in summaries.items()
            if v.get("importance", 0) >= MIN_IMPORTANCE}
    print(f"[news] 요약 {len(summaries)}건 → 중요도 {MIN_IMPORTANCE} 이상 {len(kept)}건 "
          f"/ 링크 {len(short)}건 / 테마 코멘트 {len(sector_notes)}건")
    return ({tk: {"note": v["note"], "url": short.get(tk, ""),
                  "importance": v.get("importance", 0)}
             for tk, v in kept.items()}, sector_notes)


# ------------------------------------------------------- 기업 개요 번역
# 야후가 주는 미국 기업 개요는 영문이다. 한 종목씩 부르면 호출이 백 번 넘게
# 나가므로, 전 종목을 한 번에 보내 JSON 으로 돌려받는다. 실패하면 원문을
# 그대로 쓴다(개요가 사라지는 것보다 영문이 낫다).
TRANSLATE_SYSTEM = (
    "너는 증권사 리서치 어시스턴트다. 영문 기업 개요를 한국어로 옮긴다.\n"
    "- 무엇을 만들어 어디서 돈을 버는 회사인지가 먼저 오게 쓴다.\n"
    "- 두 문장 이내, 각 문장은 명사형이나 '~한다'로 끝낸다.\n"
    "- 제품·브랜드 고유명사는 원문 표기를 유지한다(iPhone, Azure).\n"
    "- 설립연도·본사 위치처럼 주가와 무관한 정보는 버린다.\n"
    "- 의역하지 말고 원문에 없는 사실을 지어내지 않는다."
)
TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"ticker": {"type": "string"},
                               "ko": {"type": "string"}},
                "required": ["ticker", "ko"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _translate_prompt(items: list[tuple[str, str]]) -> str:
    lines = ["다음 기업 개요를 한국어로 옮겨라. 티커는 그대로 돌려준다.", ""]
    for ticker, text in items:
        lines.append(f"[{ticker}] {text}")
    return "\n".join(lines)


def _translated(text: str) -> dict[str, str]:
    """모델 응답에서 {티커: 한국어} 만 뽑는다."""
    try:
        data = json.loads(_json_block(text))
    except (json.JSONDecodeError, TypeError):
        return {}
    out = {}
    for it in (data.get("items") or []):
        t, ko = (it.get("ticker") or "").strip(), (it.get("ko") or "").strip()
        if t and ko:
            out[t] = ko
    return out


# 11섹터 × 10종목이면 110개다. 한 번에 보내면 응답이 max_tokens 를 넘겨
# 잘린 JSON 이 오고, 헤드리스 CLI 로는 5분 상한도 넘긴다(실제로 발송이
# 타임아웃됐다). 작게 쪼개 동시에 보내면 벽시계 시간은 한 덩어리치다.
TRANSLATE_CHUNK = 20
TRANSLATE_WORKERS = 6
TRANSLATE_CLI_TIMEOUT = 120     # 덩어리 하나의 상한. 넘으면 그 덩어리만 영문


def _translate_chunk(items: list[tuple[str, str]]) -> dict[str, str]:
    """한 덩어리를 번역한다. API 를 먼저 쓴다 — 도구가 필요 없는 순수 변환이라
    에이전트 루프를 도는 CLI 보다 빠르고 응답 형식도 스키마로 고정된다."""
    prompt = _translate_prompt(items)

    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            resp = anthropic.Anthropic().beta.messages.create(
                model=MODEL, max_tokens=8000,
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                system=TRANSLATE_SYSTEM,
                output_config={"effort": "low",
                               "format": {"type": "json_schema",
                                          "schema": TRANSLATE_SCHEMA}},
                messages=[{"role": "user", "content": prompt}],
            )
            got = _translated(next(b.text for b in resp.content if b.type == "text"))
            if got:
                return got
        except Exception as e:                     # noqa: BLE001
            print(f"[news] 개요 번역 실패({items[0][0]}~): {type(e).__name__}: {e}")

    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") and shutil.which("claude")):
        return {}
    instruction = (TRANSLATE_SYSTEM
                   + "\n\nJSON 만 출력한다. 설명·코드펜스 없이 JSON 객체 하나만.\n"
                     '형식: {"items": [{"ticker": "AAPL", "ko": "..."}]}\n\n'
                   + prompt)
    try:
        r = subprocess.run([shutil.which("claude"), "-p", instruction,
                            "--model", MODEL],
                           capture_output=True, text=True, encoding="utf-8",
                           timeout=TRANSLATE_CLI_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[news] 개요 번역 호출 실패({items[0][0]}~): {type(e).__name__}: {e}")
        return {}
    return _translated(r.stdout) if r.returncode == 0 else {}


def translate_profiles(profiles: dict[str, str]) -> dict[str, str]:
    """{티커: 영문 개요} -> {티커: 한국어 개요}. 실패한 종목은 빠진다."""
    items = [(t, s) for t, s in sorted(profiles.items()) if s]
    if not items:
        return {}
    chunks = [items[i:i + TRANSLATE_CHUNK]
              for i in range(0, len(items), TRANSLATE_CHUNK)]
    out = {}
    with ThreadPoolExecutor(max_workers=TRANSLATE_WORKERS) as ex:
        for got in ex.map(_translate_chunk, chunks):
            out.update(got)
    print(f"[news] 기업 개요 번역 {len(out)}/{len(items)}종목 "
          f"({len(chunks)}덩어리)")
    return out
