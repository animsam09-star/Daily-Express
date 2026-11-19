# -*- coding: utf-8 -*-
"""미국 종목 분기 실적을 회사가 발표하는 조정(Non-GAAP) 기준으로 받는다.

야후 fundamentals 는 회계 기준(GAAP)만 준다. 미국 기업은 대부분 실적 발표문에
'조정 영업이익(Adjusted / Non-GAAP operating income)' 을 따로 내고, 시장도
그 숫자로 이야기한다 — 레비티는 2분기 GAAP 영업이익률 12% 와 조정 29% 가
같은 분기의 숫자다(인수 무형자산 상각 분기 8,500만 달러가 그 차이).

조정치는 정형 데이터로 공개되지 않는다(XBRL 은 GAAP 만 담는다). 그래서
SEC 에 올라온 실적 발표 8-K(Item 2.02)의 보도자료(EX-99.1)를 받아,
조정치 표 근처만 잘라 Claude 에게 읽힌다. 보도자료 한 건에 이번 분기와
전년 동기가 함께 있어서 한 번 읽으면 두 분기가 채워진다.

보도자료는 한 번 나오면 바뀌지 않으므로 접수번호(accession)로 캐시한다.
새 분기 보도자료만 새로 읽는다.

조정치를 내지 않는 회사(애플처럼 GAAP 만 쓰는 곳, 은행)와 8-K 대신 6-K 를
내는 외국 회사는 GAAP 그대로 둔다 — 화면에서 GAAP 로 표시한다. 다만 6-K 도
본문을 훑어 실적 발표일만은 찾는다(차트 E 표시).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

# SEC 는 자동 요청에 연락처가 든 User-Agent 를 요구한다. 개인 주소를 싣지
# 않으려고 저장소 이름과 도달하지 않는 주소를 쓴다(실측: 이걸로 응답한다).
UA = {"User-Agent": "Daily-Express market brief research@daily-express.invalid",
      "Accept-Encoding": "gzip, deflate"}
TIMEOUT = 25
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/"

MODEL = os.environ.get("NONGAAP_MODEL") or "claude-sonnet-5-5"
CLI_TIMEOUT = 240
WORKERS = 4
BUDGET = int(os.environ.get("NONGAAP_BUDGET") or 240)   # 초. 넘기면 다음 실행으로
VER = 1
RELEASES = 4             # 종목마다 최근 몇 번의 실적 보도자료를 읽나(4 → 전년 동기까지 6분기)
LOOKBACK_DAYS = 420      # 분기용 — 그보다 오래된 보도자료는 보지 않는다
# 연간은 4분기(회계연도 말) 보도자료에만 있다. 한 건에 올해·작년 연간이 같이
# 있으므로 최근 4분기 보도자료와 2년 전 것을 읽으면 4개 연도가 찬다.
ANNUAL_LOOKBACK_DAYS = 1150
ANNUAL_RELEASES = 2      # 최근 4분기 보도자료 다음으로 몇 해 전 것까지 더 읽나
# 8-K 2.02 가 없는 회사의 E 표시: 차트가 2년이므로 그만큼 6-K 등을 훑는다.
# 6-K 를 많이 내는 회사(PUK 는 자사주 공시로 1년에 수백 건)가 있어 실행마다
# 시간 상한을 두고 새것부터 읽는다 — 결과는 접수번호로 캐시해 다시 안 읽는다.
ALT_LOOKBACK_DAYS = 760
ALT_MIN = 3              # 최근 LOOKBACK_DAYS 동안 8-K 2.02 가 이보다 적으면 다른 공시를 본다
ALT_BUDGET = int(os.environ.get("ALT_BUDGET") or 150)
ALT_DEDUP_DAYS = 7       # 같은 실적을 여러 공시로 낸 경우(보도자료 + 분기 보고서) 하나로
SNIP_MAX = 16000         # Claude 에게 넘기는 발췌 상한
MATCH_DAYS = 20          # 회사 분기말(10/4)과 야후 분기말(9/30)을 같은 분기로 볼 거리

_sec_lock = threading.Lock()
_sec_last = [0.0]


def _sec_get(url: str):
    """SEC 는 초당 10건 제한이다 — 스레드가 여럿이어도 간격을 둔다."""
    with _sec_lock:
        wait = 0.12 - (time.monotonic() - _sec_last[0])
        if wait > 0:
            time.sleep(wait)
        _sec_last[0] = time.monotonic()
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r


# ------------------------------------------------------------ SEC 목록
def _ciks() -> dict:
    data = _sec_get(TICKERS_URL).json()
    return {str(v["ticker"]).upper().replace(".", "-"): int(v["cik_str"])
            for v in data.values()}


def _releases(cik: int, since: dt.date) -> tuple[list[dict], str, list[dict]]:
    """최근 실적 발표 8-K(Item 2.02) 목록(새것부터), 회계연도 말(MMDD),
    그리고 실적 발표일 수 있는 다른 공시(6-K, Item 2.02 없는 8-K) 목록.

    뒤의 것은 8-K 2.02 를 안 내는 회사(Spotify·SAP 같은 외국 회사는 6-K,
    OKLO 는 7.01/8.01)의 차트 E 표시용이다 — 본문을 읽어 실적인지 가린다."""
    sub = _sec_get(SUBMISSIONS.format(cik=cik)).json()
    fye = str(sub.get("fiscalYearEnd") or "")
    r = (sub.get("filings") or {}).get("recent") or {}
    out, alt, per = [], [], []
    alt_since = dt.date.today() - dt.timedelta(days=ALT_LOOKBACK_DAYS)
    for form, acc, date, items in zip(r.get("form", []), r.get("accessionNumber", []),
                                      r.get("filingDate", []), r.get("items", [])):
        d = dt.date.fromisoformat(date)
        if d < since and d < alt_since:
            break
        if form in ("8-K", "8-K/A") and "2.02" in (items or ""):
            if d >= since:
                out.append({"acc": acc, "filed": date})
        elif d >= alt_since and (form == "6-K" or (
                form == "8-K" and re.search(r"7\.01|8\.01", items or ""))):
            alt.append({"acc": acc, "filed": date})
        elif d >= alt_since and form in ("10-Q", "10-K"):
            per.append({"acc": acc, "filed": date, "src": form})
    return out, fye, alt + per


# 실적 발표문인지 — 머리말(제목·표지의 첨부 목록)에 '실적을 냈다' 는 문구가
# 있는가. 예고("3분기 실적을 ○일 발표한다")·사전 안내(XOM 'Earnings
# Considerations', DB 'Key updates')·주총 안내가 그보다 먼저 나오면 아니다.
_EARN_POS = [re.compile(x, re.I) for x in (
    r"\b(reports?|reported|announces?|delivers?|posts?|publishes)\b[^.\n]{0,90}?"
    r"\b(results|earnings|profits?)\b",
    r"\b(q[1-4]|[1-4]q|first|second|third|fourth|half[- ]year|first[- ]half|full[- ]year|"
    r"interim|quarterly|annual)\b[^.\n]{0,25}?\b(results|earnings|profits?)\b",
    r"\bearnings report as of\b|\binterim report\b|"
    r"\binterim (condensed )?(consolidated )?financial statements\b|"
    r"management.s discussion and analysis for the (quarter|three|six|nine|year)",
    r"\b(results|earnings)\s+for\s+the\s+(first|second|third|fourth|full|fiscal|[1-4]q|q[1-4]|"
    r"three|six|nine|twelve|half|year|quarter|period)\b")]
_EARN_NEG = re.compile(
    r"planned impacts to earnings|earnings considerations|key updates communicated|"
    r"\bwill (report|announce|release|publish|host|hold)\b|"
    r"\bto (report|announce|release) (its|their)\b|notice of|annual general meeting|"
    r"information circular|expected increase|\bpricing of\b|"
    r"announces? (the )?(date|timing)|conference call (and webcast )?(to|on|scheduled)", re.I)
EARN_HEAD = 4000
_EARN_NUM = re.compile(r"\b(revenues?|net (income|profit|loss)|operating (income|profit|loss)|"
                       r"per share|ebitda|profit before tax|net sales)\b", re.I)


def _is_earnings(text: str) -> bool:
    if len(text) < 2500:
        return False
    head = text[:EARN_HEAD]
    pos = [m.start() for rx in _EARN_POS for m in [rx.search(head)] if m]
    if not pos:
        return False
    neg = _EARN_NEG.search(head[:600])
    return not neg or min(pos) < neg.start()


def _earn_score(text: str) -> int:
    """실적 발표문다움 — 앞부분의 재무 항목 수. 한 분기 안에 실적으로 보이는
    공시가 여럿이면 이 값이 큰 것을 남긴다(보도자료·보고서가 자사주·채권
    공시보다 크다)."""
    return len(_EARN_NUM.findall(text[:20000]))


EARN_FILE = re.compile(r"earn|result|q[1-4]|[1-4]q|interim|quarter|half|press|release|"
                       r"ex-?99|exhibit-?99|99-?1|991", re.I)


def _alt_earnings(cik: int, acc: str) -> int:
    """6-K 등 한 건이 실적 발표인가. 첨부가 여럿이면(표지·보도자료·보고서)
    실적일 법한 이름부터 세 개까지 본다. 실적이 아니면 0, 맞으면 1 + 점수."""
    base = ARCHIVE.format(cik=cik, acc=acc.replace("-", ""))
    idx = _sec_get(base + "index.json").json()
    files = [f for f in (idx.get("directory") or {}).get("item") or []
             if f.get("name", "").lower().endswith((".htm", ".html"))
             and "index" not in f["name"].lower()
             and not re.fullmatch(r"R\d+\.htm", f["name"])]
    files.sort(key=lambda f: (not EARN_FILE.search(f["name"]), int(f.get("size") or 0)))
    best = 0
    for f in files[:3]:
        t = _clean(_sec_get(base + f["name"]).text)
        if _is_earnings(t):
            best = max(best, 1 + _earn_score(t))
    return best


EX_NAME = re.compile(r"ex-?99|exhibit-?99|press|release|earnings|results", re.I)


def _release_text(cik: int, acc: str) -> str:
    base = ARCHIVE.format(cik=cik, acc=acc.replace("-", ""))
    idx = _sec_get(base + "index.json").json()
    files = [f for f in (idx.get("directory") or {}).get("item") or []
             if f.get("name", "").lower().endswith((".htm", ".html"))
             and "index" not in f["name"].lower()
             and not re.fullmatch(r"R\d+\.htm", f["name"])]      # XBRL 뷰어 조각
    # 보도자료가 EX-99.1 이다. 이름이 제각각이라(ex99-1.htm, q32026pressrelease.htm)
    # 이름으로 먼저 고르고, 없으면 본문(8-K 표지)이 아닌 가장 큰 파일을 쓴다.
    named = [f for f in files if EX_NAME.search(f["name"])]
    pool = named or files
    if not pool:
        return ""
    pick = max(pool, key=lambda f: int(f.get("size") or 0))
    return _clean(_sec_get(base + pick["name"]).text)


def _clean(raw: str) -> str:
    # 인라인 XBRL 의 숨은 머리(ix:header)는 본문 앞에 태그 값을 줄줄이 늘어놓는다
    t = re.sub(r"(?is)<ix:header>.*?</ix:header>", " ", raw)
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", t)
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()


KEY = re.compile(r"non-?gaap|adjusted|operating (income|profit|earnings)|"
                 r"net (income|earnings)|diluted|per share|revenue|net sales",
                 re.I)


def _snippet(text: str) -> str:
    """머리말(분기·주요 수치)과 조정치 표 주변만 남긴다 — 보도자료는 길다."""
    head = text[:3500]
    spans = []
    for m in KEY.finditer(text, 3500):
        a, b = max(3500, m.start() - 500), min(len(text), m.end() + 900)
        if spans and a <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    # 조정치 대사표는 대개 뒤쪽에 있다. 'adjusted'·'non-GAAP' 가 든 구간을
    # 먼저 넣고 나머지로 채운다.
    def score(s):
        seg = text[s[0]:s[1]].lower()
        return -(seg.count("adjusted") + seg.count("non-gaap") * 2)
    out, room = [head], SNIP_MAX - len(head)
    for a, b in sorted(spans, key=score):
        if room <= 0:
            break
        seg = text[a:b][:room]
        out.append(seg)
        room -= len(seg)
    return "\n…\n".join(out)


# ------------------------------------------------------------ Claude
PROMPT = """\
Below is an excerpt of a US company's quarterly earnings press release.
Extract the figures for the THREE-MONTH period (not year-to-date, not full year)
of the quarter this release reports, and for the same quarter one year earlier;
and, if the release covers a fiscal-year-end quarter, the full fiscal year figures too.

Return ONLY JSON:
{{"period_end": "YYYY-MM-DD", "unit": "ones|thousands|millions|billions",
  "cur":   {{"revenue": n, "op_gaap": n, "op_adj": n, "net_gaap": n, "net_adj": n, "eps_adj": n}},
  "prior": {{"period_end": "YYYY-MM-DD", "revenue": n, "op_gaap": n, "op_adj": n,
             "net_gaap": n, "net_adj": n, "eps_adj": n}},
  "fy":       {{"period_end": "YYYY-MM-DD", "revenue": n, "op_gaap": n, "op_adj": n,
               "net_gaap": n, "net_adj": n, "eps_adj": n}} or null,
  "fy_prior": {{"period_end": "YYYY-MM-DD", "revenue": n, "op_gaap": n, "op_adj": n,
               "net_gaap": n, "net_adj": n, "eps_adj": n}} or null}}

Rules:
- period_end: last day of the fiscal quarter as stated (e.g. "October 4, 2026" -> "2026-10-04").
- unit: the unit the money figures are stated in, as the release's tables say
  ("in millions", "in thousands"). All money numbers use that one unit. eps_adj is per share in dollars.
- revenue: total revenue / net sales (GAAP, as reported).
- op_gaap: GAAP operating income (loss).
- op_adj: the company's own non-GAAP operating income: "Adjusted operating income",
  "Non-GAAP operating income", "Adjusted operating profit", "Adjusted EBIT",
  "Operating income, as adjusted". NOT adjusted EBITDA, NOT segment profit unless the
  company labels the segment total as its adjusted operating income.
- net_adj: non-GAAP / adjusted net income attributable to the company.
- eps_adj: non-GAAP / adjusted diluted EPS.
- fy / fy_prior: ONLY if this release reports a fiscal-year-end quarter AND prints
  full fiscal year (twelve months / year ended) figures — the fiscal year just ended and
  the year before it. Otherwise null. Do not put year-to-date figures of a partial year here.
- Use null for anything the excerpt does not state. Never compute or estimate a value
  that is not printed. Losses are negative numbers.
Ticker: {ticker}

{text}
"""


# 답은 왔는데 읽을 수 없던 보도자료(단위·분기말을 못 찾음 등). 다시 물어도
# 같은 답이라 실패로 남겨 두면 매 실행 예산만 먹는다 — 한 번으로 끝낸다.
UNREADABLE = {"unreadable": True}


def _ask(prompt: str) -> dict | None:
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") and shutil.which("claude"):
        try:
            r = subprocess.run([shutil.which("claude"), "-p", prompt, "--model", MODEL],
                               capture_output=True, text=True, encoding="utf-8",
                               timeout=CLI_TIMEOUT)
            if r.returncode == 0:
                return _parse(r.stdout) or UNREADABLE
            print(f"[조정실적] Claude Code 오류(exit {r.returncode})")
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"[조정실적] Claude Code 호출 실패: {type(e).__name__}")
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            resp = anthropic.Anthropic().messages.create(
                model=MODEL, max_tokens=1200,
                messages=[{"role": "user", "content": prompt}])
            return _parse("".join(b.text for b in resp.content
                                  if getattr(b, "type", "") == "text")) or UNREADABLE
        except Exception as e:                     # noqa: BLE001
            print(f"[조정실적] API 실패: {type(e).__name__}")
    return None


SCALE = {"ones": 1, "thousands": 1e3, "millions": 1e6, "billions": 1e9}
FIELDS = ("revenue", "op_gaap", "op_adj", "net_gaap", "net_adj", "eps_adj")


def _scale(unit):
    """'millions' 뿐 아니라 'in millions', 'USD millions', 'dollars' 도 받는다."""
    u = str(unit or "").lower()
    for word, k in (("billion", 1e9), ("million", 1e6), ("thousand", 1e3)):
        if word in u:
            return k
    if u.strip() in ("ones", "one", "dollars", "usd", "units", "$"):
        return 1
    return None


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _parse(out: str) -> dict | None:
    s = (out or "").strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(s[i:j + 1])
    except ValueError:
        return None
    k = _scale(d.get("unit"))
    if not k:
        return None

    def block(b, end):
        if not isinstance(b, dict) or not _iso(end):
            return None
        row = {"end": end}
        for f in FIELDS:
            v = _num(b.get(f))
            row[f] = v if (v is None or f == "eps_adj") else v * k
        # 상식 검사: 조정 영업이익이 매출보다 크면 단위·칸을 잘못 읽은 것이다
        rev = row.get("revenue")
        for f in ("op_adj", "net_adj"):
            if rev and row.get(f) is not None and abs(row[f]) > rev * 1.2:
                row[f] = None
        return row if any(row.get(f) is not None for f in FIELDS) else None

    cur = block(d.get("cur"), d.get("period_end"))
    prior = block(d.get("prior"), (d.get("prior") or {}).get("period_end"))
    fy = block(d.get("fy"), (d.get("fy") or {}).get("period_end"))
    fyp = block(d.get("fy_prior"), (d.get("fy_prior") or {}).get("period_end"))
    if not cur:
        return None
    # 연간 매출이 분기 매출보다 작으면 연간을 잘못 읽은 것이다
    yrs = [r for r in (fy, fyp) if r and not (r.get("revenue") and cur.get("revenue")
                                             and r["revenue"] < cur["revenue"] * 2.5)]
    return {"rows": [r for r in (cur, prior) if r], "years": yrs, "fy_checked": True}


def _iso(s) -> bool:
    try:
        dt.date.fromisoformat(str(s))
        return True
    except ValueError:
        return False


# ------------------------------------------------------------ 캐시
def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("items") or {} if d.get("ver") == VER else {}
    except Exception:                              # noqa: BLE001
        return {}


def save(path: str, items: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ver": VER, "items": items}, f, ensure_ascii=False, indent=0)
    except Exception as e:                         # noqa: BLE001
        print(f"[조정실적] 캐시 저장 실패: {type(e).__name__}")


# ------------------------------------------------------------ 본체
def build(tickers, path: str) -> dict:
    """{티커: [{end, revenue, op_gaap, op_adj, net_gaap, net_adj, eps_adj}, ...]}.

    캐시 키는 접수번호다. 값은 {ticker, filed, rows} 또는 {ticker, none: True}
    (읽었는데 조정치가 없던 보도자료 — 다시 읽지 않는다).
    """
    want = sorted({t.upper() for t in (tickers or [])})
    cache = load(path)
    try:
        ciks = _ciks()
    except Exception as e:                         # noqa: BLE001
        print(f"::warning::[조정실적] SEC 티커 목록 실패: {type(e).__name__} — 캐시만 쓴다")
        return _assemble(cache, want, None)

    today = dt.date.today()
    since = today - dt.timedelta(days=LOOKBACK_DAYS)
    since_y = today - dt.timedelta(days=ANNUAL_LOOKBACK_DAYS)
    todo, no_cik = [], []

    def scan(t):
        cik = ciks.get(t)
        if not cik:
            return t, None, [], "", []
        try:
            rels, fye, alt = _releases(cik, since_y)
            return t, cik, rels, fye, alt
        except Exception as e:                     # noqa: BLE001
            print(f"[조정실적] {t} 목록 실패: {type(e).__name__}")
            return t, cik, [], "", []

    def is_fy_end(v, fye):
        """캐시된 보도자료의 이번 분기말이 회계연도 말(±20일)인가."""
        rows = (v or {}).get("rows") or []
        if not rows or len(fye) != 4:
            return False
        end = dt.date.fromisoformat(rows[0]["end"])
        for y in (end.year - 1, end.year, end.year + 1):
            try:
                f = dt.date(y, int(fye[:2]), int(fye[2:]))
            except ValueError:
                continue
            if abs((end - f).days) <= MATCH_DAYS:
                return True
        return False

    filed: dict[str, list] = {}     # 종목별 실적 발표 8-K (차트의 E 표시용)
    alt_todo, alt_have = [], {}      # 8-K 2.02 가 없는 회사의 6-K 등
    with ThreadPoolExecutor(max_workers=6) as ex:
        for t, cik, rels, fye, alt in ex.map(scan, want):
            if rels:
                filed[t] = rels
            if alt and sum(dt.date.fromisoformat(r["filed"]) >= since
                           for r in rels) < ALT_MIN:
                alt_have[t] = alt
                alt_todo += [(t, cik, r) for r in alt
                             if not r.get("src") and r["acc"] not in cache]
            if cik is None:
                no_cik.append(t)
                continue
            recent = [r for r in rels if dt.date.fromisoformat(r["filed"]) >= since][:RELEASES]
            for r in recent:
                v = cache.get(r["acc"])
                # 연간 칸이 생기기 전에 읽은 4분기 보도자료는 연간을 위해 다시 읽는다
                if v is None or (is_fy_end(v, fye) and not v.get("fy_checked")):
                    todo.append((t, cik, r, 0))
            # 연간용: 최근 4분기 보도자료를 알면 1년·2년 전 같은 철 보도자료를 더 읽는다
            q4 = next((r for r in recent if is_fy_end(cache.get(r["acc"]), fye)), None)
            if q4:
                # 1년 전 4분기 보도자료는 건너뛴다 — 그 해는 최근 보도자료의
                # '작년' 칸에 이미 있다. 2년 전 것이 그 앞의 두 해를 채운다.
                f0 = dt.date.fromisoformat(q4["filed"])
                k = ANNUAL_RELEASES
                near = [r for r in rels
                        if abs((dt.date.fromisoformat(r["filed"]) - f0).days + 365 * k) <= 45]
                if near and near[0]["acc"] not in cache:
                    todo.append((t, cik, near[0], 1))
    # 분기용 최근 보도자료가 먼저, 그 안에서는 새것부터 — 예산에 걸려 넘어가는
    # 것은 오래된 연간용이다
    todo.sort(key=lambda x: (x[3], -dt.date.fromisoformat(x[2]["filed"]).toordinal()))
    print(f"::notice::[조정실적] {len(want)}종목 중 SEC 8-K 대상 {len(want) - len(no_cik)}종목"
          f" · 새로 읽을 보도자료 {len(todo)}건 (6-K 등 제외 {len(no_cik)}종목)")

    deadline = time.monotonic() + BUDGET
    done = skipped = 0

    def one(item):
        t, cik, r, _ = item
        if time.monotonic() > deadline:
            return item, None, True
        try:
            text = _release_text(cik, r["acc"])
        except Exception as e:                     # noqa: BLE001
            print(f"[조정실적] {t} 보도자료 실패: {type(e).__name__}")
            return item, None, True
        if not text:
            return item, {"ticker": t, "filed": r["filed"], "none": True}, False
        got = _ask(PROMPT.format(ticker=t, text=_snippet(text)))
        if got is None:
            return item, None, True                # 호출 실패 — 다음 실행에서 다시
        if got.get("unreadable"):
            return item, {"ticker": t, "filed": r["filed"], "none": True}, False
        return item, {"ticker": t, "filed": r["filed"], **got}, False

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for (t, cik, r, _), val, retry in ex.map(one, todo):
            if retry:
                skipped += 1
                continue
            cache[r["acc"]] = val
            done += 1
    save(path, cache)
    print(f"[조정실적] 새로 읽은 보도자료 {done}건 (캐시 {len(cache)}건)"
          + (f" — {skipped}건은 다음 실행으로" if skipped else ""))
    _scan_alt(cache, alt_todo)
    save(path, cache)
    for t, alt in alt_have.items():
        got = [{**r, "score": (cache.get(r["acc"]) or {}).get("score") or 1}
               for r in alt if (cache.get(r["acc"]) or {}).get("earn")]
        ev = _pick_quarterly(sorted((filed.get(t) or []) + got, key=lambda r: r["filed"]))
        ev = _fill_periodic(ev, [r for r in alt if r.get("src")])
        if ev:
            filed[t] = ev
    return _assemble(cache, want, filed)


ALT_CLUSTER_DAYS = 50    # 분기 실적은 석 달 간격 — 이 안에 붙은 후보는 한 분기다


def _pick_quarterly(rels: list[dict]) -> list[dict]:
    """날짜순 후보를 ALT_CLUSTER_DAYS 단위로 묶어 묶음마다 하나만 남긴다.
    8-K 2.02(점수 없음)가 있으면 그것, 없으면 점수가 큰 것(같으면 앞의 것)."""
    out, grp = [], []
    for r in rels + [None]:
        if r is not None and grp and (dt.date.fromisoformat(r["filed"])
                                      - dt.date.fromisoformat(grp[0]["filed"])).days <= ALT_CLUSTER_DAYS:
            grp.append(r)
            continue
        if grp:
            pick = max(grp, key=lambda x: (x.get("score") is None, x.get("score") or 0,
                                           -dt.date.fromisoformat(x["filed"]).toordinal()))
            out.append({k: v for k, v in pick.items() if k != "score"})
        grp = [r] if r is not None else []
    return out


def _fill_periodic(ev: list[dict], per: list[dict]) -> list[dict]:
    """실적 발표 공시를 못 찾은 분기를 분기·연간 보고서(10-Q/10-K) 제출일로
    채운다 — OKLO·XOM 처럼 실적을 8-K 2.02 로 안 내는 미국 회사. 보도자료보다
    며칠 늦을 수 있어 화면에서 '보고서 제출' 로 따로 적는다. 앞 45일 안에
    발표가 이미 있는 보고서는 그 분기 것이라 넣지 않는다."""
    if not per:
        return ev
    have = [dt.date.fromisoformat(r["filed"]) for r in ev]
    add = []
    for r in per:
        d = dt.date.fromisoformat(r["filed"])
        if not any(0 <= (d - h).days <= 45 for h in have):
            add.append(r)
    return sorted(ev + add, key=lambda r: r["filed"])


def _dedup(rels: list[dict]) -> list[dict]:
    """날짜순 목록에서 ALT_DEDUP_DAYS 안에 붙은 것은 앞의 것만 남긴다."""
    out = []
    for r in rels:
        d = dt.date.fromisoformat(r["filed"])
        if out and (d - dt.date.fromisoformat(out[-1]["filed"])).days <= ALT_DEDUP_DAYS:
            continue
        out.append(r)
    return out


def _scan_alt(cache: dict, todo: list) -> None:
    """6-K 등을 읽어 실적 발표문인지만 가린다(Claude 없이). 결과는
    {"ticker", "filed", "alt": True, "earn": bool} 로 캐시한다."""
    if not todo:
        return
    todo.sort(key=lambda x: x[2]["filed"], reverse=True)
    deadline = time.monotonic() + ALT_BUDGET

    def one(item):
        t, cik, r = item
        if time.monotonic() > deadline:
            return item, None
        try:
            earn = _alt_earnings(cik, r["acc"])
        except Exception:                          # noqa: BLE001
            return item, None
        return item, {"ticker": t, "filed": r["filed"], "alt": True, "earn": earn > 0,
                      "score": earn}

    n = hit = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        for (t, cik, r), v in ex.map(one, todo):
            if v is None:
                continue
            cache[r["acc"]] = v
            n += 1
            hit += v["earn"]
    print(f"::notice::[실적일] 8-K 2.02 없는 종목의 공시 {n}/{len(todo)}건 확인 — 실적 {hit}건"
          + (" (나머지는 다음 실행)" if n < len(todo) else ""))


def _assemble(cache: dict, want, filed: dict | None) -> dict:
    """{티커: {"q": [분기 행], "y": [연간 행], "e": [실적 발표일]}}

    e 는 차트에 실적 발표일(E)을 찍는 재료다 — SEC 실적 8-K 접수일(보도자료가
    나온 날)과, 읽어 둔 보도자료면 그 분기 조정 EPS·전년 동기 EPS. SEC 목록을
    못 받았으면 캐시에 남은 보도자료 날짜로 대신한다."""
    qs = _assemble_key(cache, want, "rows")
    ys = _assemble_key(cache, want, "years")
    if filed is None:
        filed = {}
        for acc, v in cache.items():
            if v.get("ticker") in set(want) and (not v.get("alt") or v.get("earn")):
                filed.setdefault(v["ticker"], []).append({"acc": acc, "filed": v["filed"]})
        filed = {t: _dedup(sorted(rs, key=lambda r: r["filed"])) for t, rs in filed.items()}
    out = {}
    for t in set(qs) | set(filed):
        ev = []
        for r in sorted(filed.get(t) or [], key=lambda r: r["filed"]):
            v = cache.get(r["acc"]) or {}
            rows = v.get("rows") or []
            cur = rows[0] if rows else {}
            prior = rows[1] if len(rows) > 1 else {}
            ev.append({"d": r["filed"], "q": cur.get("end"), "src": r.get("src"),
                       "eps": cur.get("eps_adj"), "eps_py": prior.get("eps_adj"),
                       "rev": cur.get("revenue"), "rev_py": prior.get("revenue")})
        out[t] = {"q": qs.get(t, []), "y": ys.get(t, []), "e": ev}
    return out


def _assemble_key(cache: dict, want, key: str) -> dict:
    """종목별로 분기 행을 모은다. 같은 분기가 두 보도자료에 나오면(이번 분기
    표와 1년 뒤 보도자료의 전년 동기 칸) 나중에 낸 쪽을 쓴다 — 재분류가
    반영돼 있다."""
    w = set(want)
    out: dict[str, dict] = {}
    for v in sorted(cache.values(), key=lambda v: v.get("filed") or ""):
        t = v.get("ticker")
        if t not in w or v.get("none"):
            continue
        for row in v.get(key) or []:
            out.setdefault(t, {})[row["end"]] = row
    return {t: sorted(d.values(), key=lambda r: r["end"]) for t, d in out.items()}


def merge(holding: dict, adj) -> None:
    """야후 분기·연간 표에 조정치를 붙인다. adj 는 {"q": [...], "y": [...]}.

    분기말이 며칠 어긋나는 회사(52주 회계연도)가 있어 가장 가까운 칸과
    MATCH_DAYS 안이면 맞춘다. 야후에 아직 없는 최근 분기(10-Q 전)는 prov_adj 로
    따로 넘겨 '잠정' 칸이 영업이익·순이익까지 채우게 한다."""
    if not adj:
        return
    if isinstance(adj, list):                     # 옛 모양(분기만)
        adj = {"q": adj, "y": []}
    if adj.get("e"):
        holding["earn_dates"] = adj["e"]
    prov = _attach(holding.get("quarters") or [], adj.get("q") or [])
    if prov:
        holding["prov_adj"] = {k: prov.get(k) for k in ("end", *FIELDS)}
    _attach(holding.get("annuals") or [], adj.get("y") or [])


def _attach(qs: list[dict], rows: list[dict]):
    """rows 를 qs 칸에 붙이고, qs 마지막 칸보다 뒤에 있는 가장 최근 행을 돌려준다."""
    dates = [dt.date.fromisoformat(q["date"]) for q in qs]
    newer = None
    for row in rows:
        end = dt.date.fromisoformat(row["end"])
        best = min(range(len(dates)), key=lambda i: abs((dates[i] - end).days),
                   default=None)
        if best is not None and abs((dates[best] - end).days) <= MATCH_DAYS:
            q = qs[best]
            # 매출이 크게 어긋나면 다른 기간·다른 기준을 읽은 것이다
            if (q.get("revenue") and row.get("revenue")
                    and abs(row["revenue"] / q["revenue"] - 1) > 0.06):
                continue
            for k in ("op_adj", "net_adj", "eps_adj"):
                if row.get(k) is not None:
                    q[k] = row[k]
        elif not dates or end > max(dates):
            newer = row                            # rows 는 날짜순 — 마지막이 최신
    return newer
