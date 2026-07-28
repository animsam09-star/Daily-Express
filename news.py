"""섹터 상위 종목의 관련 뉴스 수집 + Claude 한국어 요약 + 링크 축약.

주가가 왜 움직였는지는 사후 해석이라 자동으로 단정하면 틀린 설명이 나간다.
그래서 이 모듈은 '이유'를 만들어내지 않는다. 실제 수집한 헤드라인만 근거로 삼고,
헤드라인이 등락을 설명하지 못하면 그렇다고 답하게 한다. 요약마다 근거가 된
기사 링크를 함께 붙여 사용자가 직접 확인할 수 있게 한다.

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

HEADLINES_PER_TICKER = 4
MODEL = "claude-opus-5"
CLI_TIMEOUT = 300           # Claude Code 헤드리스 호출 상한(초)

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
                        "description": "한국어 한 줄 요약(35자 내외). 헤드라인이 "
                                       "등락을 설명하지 못하면 빈 문자열.",
                    },
                    "source": {
                        "type": "integer",
                        "description": "근거로 삼은 헤드라인 번호. 없으면 -1.",
                    },
                },
                "required": ["ticker", "note", "source"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

SYSTEM = """너는 한국 증권사 리서치 어시스턴트다. 미국 주식의 당일 등락과 번호가 매겨진
관련 뉴스 헤드라인을 받아, 종목마다 한국어 한 줄 메모를 만든다.

지켜야 할 규칙:
1. 반드시 제공된 헤드라인에만 근거한다. 헤드라인에 없는 사실·수치·배경을 지어내지 않는다.
2. 헤드라인이 그 종목의 등락과 무관하거나 설명이 안 되면 note 를 빈 문자열,
   source 를 -1 로 둔다. 억지로 채우지 않는 것이 맞는 답이다.
3. 인과를 단정하지 않는다. "~때문에 급락"이 아니라 "~보도" 처럼 사실만 적는다.
4. 35자 내외로 짧게. 종목명은 반복하지 말고 내용만 적는다.
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
   비워 두는 것이 틀린 설명을 적는 것보다 낫다."""


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


def _rank(arts: list[dict]) -> list[dict]:
    """데이터 페이지는 버리고, 원인이 담긴 기사를 앞으로 보낸다."""
    kept = [a for a in arts if not DATA_PAGE_RE.search(a["title"])]
    kept.sort(key=lambda a: 1 if _is_recap(a["title"]) else 0)
    return kept[:HEADLINES_PER_TICKER]


def _headlines(ticker: str, name: str) -> list[dict]:
    """Finnhub(티커 직접) → 구글 뉴스 RSS(지정 매체) → 야후 순으로 시도한다."""
    return _rank(_finnhub(ticker) or _gnews(ticker, name) or _yahoo(ticker, name))


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


def _prompt(stocks: list[dict]) -> str:
    lines = []
    for s in stocks:
        chg = f"{s['chg_pct']:+.2f}%" if s.get("chg_pct") is not None else "n/a"
        lines.append(f"[{s['ticker']}] {s['name']} {chg}")
        for i, h in enumerate(s["headlines"]):
            lines.append(f"  {i}. {h['title']}")
    return "\n".join(lines)


def _parse(text: str) -> dict[str, dict]:
    """모델 출력에서 {티커: {note, source}}. 앞뒤 잡텍스트가 있어도 JSON 만 건진다."""
    text = text.strip()
    if not text.startswith("{"):
        i, j = text.find("{"), text.rfind("}")
        if i == -1 or j == -1:
            return {}
        text = text[i:j + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[news] 요약 파싱 실패: {e}")
        return {}
    out = {}
    for it in data.get("items", []):
        note = (it.get("note") or "").strip()
        if it.get("ticker") and note:
            out[it["ticker"]] = {"note": note, "source": it.get("source", -1)}
    return out


def _via_claude_code(stocks: list[dict]) -> dict | None:
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
          '형식: {"items": [{"ticker": "TICKER", "note": "한 줄 메모", "source": 0}]}\n\n'
        + _prompt(stocks)
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


def _via_api(stocks: list[dict]) -> dict:
    """Anthropic API 키 경로. 사용량만큼 과금된다."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {}
    try:
        import anthropic
    except ImportError:
        print("[news] anthropic 패키지 없음 — 요약 생략")
        return {}
    try:
        resp = anthropic.Anthropic().beta.messages.create(
            model=MODEL, max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            system=SYSTEM,
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}},
            messages=[{"role": "user", "content": _prompt(stocks)}],
        )
    except Exception as e:                     # noqa: BLE001
        print(f"[news] 요약 호출 실패: {type(e).__name__}: {e}")
        return {}
    if resp.stop_reason == "refusal":
        print("[news] 요약 거부됨 — 헤드라인만 사용")
        return {}
    try:
        return _parse(next(b.text for b in resp.content if b.type == "text"))
    except StopIteration:
        return {}


def build(holdings: dict) -> dict[str, dict]:
    """{티커: {note, url}}. 어느 단계가 실패해도 발송은 막지 않는다."""
    try:
        stocks = collect(holdings)
    except Exception as e:                     # noqa: BLE001
        print(f"[news] 헤드라인 수집 실패: {type(e).__name__}: {e}")
        return {}
    if not stocks:
        return {}
    print(f"[news] {len(stocks)}종목 헤드라인 수집 — 요약 요청")

    summaries = _via_claude_code(stocks)
    if summaries is None:
        summaries = _via_api(stocks)
    if not summaries:
        return {}

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

    print(f"[news] 요약 {len(summaries)}건 / 링크 {len(short)}건")
    return {tk: {"note": v["note"], "url": short.get(tk, "")}
            for tk, v in summaries.items()}
