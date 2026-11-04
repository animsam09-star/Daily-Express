# -*- coding: utf-8 -*-
"""어닝콜 전문을 받아 분석가 메모체로 요약한다.

전문은 모틀리풀이 무료로 공개한다. 티커로 주소를 역산할 수는 없지만
(슬러그에 회사명이 들어간다) 날짜 목록 페이지에 최근 전문이 모여 있고
슬러그의 'q{N}' 바로 앞 토큰이 티커다 — 러너에서 실측해 확인했다.
  /earnings/call-transcripts/2026/10/26/verizon-vz-q3-2026-earnings-call-transcript/
본문은 id="article-body-transcript" 안에 있고 'Read Next' 에서 끝난다.

전문은 5만 자쯤이라 한 종목에 한 번 묻는다. 콜은 분기에 한 번뿐이므로
캐시가 거의 전부를 해결한다 — 새로 올라온 것만 쓴다.
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

INDEX = "https://www.fool.com/earnings-call-transcripts/"
PAGE = INDEX + "page/{n}/"
MAX_PAGES = 90           # 목록 상한. 한 쪽에 전문 20건이고 실적 시즌에는
                         # 하루 백 건씩 올라온다 — 3주치가 대략 이만큼이다.
IDX_WORKERS = 6          # 목록은 여러 쪽을 동시에 받는다
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT = 25

LINK = re.compile(r'href="(/earnings/call-transcripts/'
                  r'(\d{4})/(\d{2})/(\d{2})/'
                  r'([a-z0-9-]+?)-q(\d)-(\d{4})-earnings-call-transcript/)"')

MODEL = "claude-opus-5"
CLI_TIMEOUT = 420
WORKERS = 4
BUDGET = 240             # 초. 넘기면 받은 것만 캐시하고 다음 실행으로 넘긴다
                         # (개요와 같은 예산 — 06:05 브리핑이 06:30 까지 나가야 한다)
VER = 2                  # 프롬프트를 바꾸면 올린다(2: 배경·용어·해석 칸)
BODY_MAX = 60000         # 전문 글자 상한(사이트가 길어져도 요청이 터지지 않게)
SINCE_DAYS = 21          # 며칠 전 콜까지 찾을 것인가(실적 발표 카드와 같은 창)

SYSTEM = """\
너는 증권사 애널리스트다. 아래 어닝콜 전문을 읽고 한국어 해설 메모를 쓴다.

읽는 사람은 이 회사를 평소에 따라가지 않는 투자자다. 숫자만 나열하면
"그래서 그게 왜 중요한데?" 를 알 수 없다. 그래서 사실(전문에 있는 것),
배경(왜 그 얘기가 나왔는가), 해석(그래서 무슨 뜻인가)을 칸을 나눠 적는다.

[들어가기 전에] intro
- what: 무슨 회사인지 두세 문장. 사업부가 있으면 부문마다 '무엇을 하는
  곳인지' 를 쉬운 말로 붙인다. 예: "Well Construction(유정 건설) —
  시추와 측정. 땅에 구멍을 뚫는 일". 아래 [회사 개요] 를 참고한다.
- drivers: 이 회사 실적을 움직이는 변수 2~4개. 예: "정제마진(원유를 사서
  휘발유·경유로 팔 때 남는 값)".

[주요 코멘트] comments 6~8개 — 사전 발표에서
- head: 결론과 핵심 수치. 예: "SI 부문 최대 성장동력 — 매출 $7.6B"
- body: 한두 문장. 증감률·비교 시점·가이던스를 전문에 있는 대로.
- context: 이게 왜 중요한지, 무슨 일이 있었는지 2~3문장.
  사건을 인과 순서로 쓴다(A 때문에 B 가 생겼고 그래서 C).

[질의응답] qa — 전문의 질문을 빠짐없이 전부. 하나도 버리지 않는다.
- who: 질문자와 소속. 예: "Neil Mehta (Goldman Sachs)". 모르면 빈 문자열.
- q: 질문을 한두 문장으로.
- a: 답변을 화자가 말한 그대로의 어투로 옮긴다. "~라고 설명했다" 같은
  보고 말투를 쓰지 않는다. 숫자는 반드시 남긴다. 답을 피했으면 피한 대로.
- context: 왜 이 시점에 이 질문이 나왔는가, 질문이 가리키는 사건이 무엇인가
  2~3문장. 전문 안(사전 발표·다른 문답)에서 근거를 찾는다. 한 문장으로
  끝나면 배경이 아니라 단어 설명이다.
- note: 이 답변에서 따라 나오는 해석 한 문장. 없으면 빈 문자열.

[용어] terms — 처음 보는 사람이 막힐 전문용어·약어·고유명사 4~8개.
- t: 용어(정식 명칭은 괄호), d: 쉬운 말 풀이 한 문장.

[미확인] unverified — 전문만으로 확인되지 않거나 산술이 맞지 않는 수치.
  없으면 빈 배열. 경영진을 비난하지 않는다(전사 오류일 수 있다).

지켜야 할 것
- 판단·평가는 note 안에서만. head·body·a·context 에 '가장 설득력 있는',
  '전략적', '약한 고리' 같은 평가 표현을 쓰지 않는다.
- 전문에 없는 숫자·전망을 지어내지 않는다. 회사 표현이 모호하면 모호한 대로.
- 한국어로 쓰고, 전문용어는 쉬운 말을 먼저 쓰고 정식 명칭을 괄호에 붙인다.
- 문장을 짧게 끊는다. 평서문 종결(-한다/-이다).
- 금액은 전문 표기를 따른다($14.6B 등). 연도는 '26년 꼴.
- 마케팅 수사('선도적','혁신적')는 걷어낸다.

JSON 만 출력한다. 설명도 코드펜스도 없이 객체 하나만.
형식: {"intro":{"what":"","drivers":[""]},
 "comments":[{"head":"","body":"","context":""}],
 "qa":[{"who":"","q":"","a":"","context":"","note":""}],
 "terms":[{"t":"","d":""}],
 "unverified":[""]}
"""


def _get(url):
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def _safe_get(url):
    """목록 한 쪽이 실패해도 나머지 쪽은 계속 읽는다."""
    try:
        return _get(url)
    except Exception as e:                         # noqa: BLE001
        print(f"[컨콜] 목록 실패({type(e).__name__}) {url[-24:]}")
        return ""


def index(want, since, max_pages: int = MAX_PAGES) -> dict:
    """{티커: {"url","date","quarter"}} — want 에 든 종목의 최근 전문.

    목록은 날짜 내림차순이라 앞쪽부터 읽다가 since 보다 오래된 쪽이 나오면
    멈춘다. 찾을 종목을 다 찾아도 멈춘다 — 실적 시즌에는 하루에 백 건씩
    올라와서 3주치를 다 읽으면 백 쪽이 넘는다.

    종목 페이지(/quote/...)에 전문 링크가 있으면 티커로 바로 갈 수 있었을
    텐데, 실측해 보니 0개였다(클라이언트에서 그린다). 그래서 목록뿐이다.
    """
    want = {t.upper() for t in want}
    out, n, pages_read = {}, 1, 0
    with ThreadPoolExecutor(max_workers=IDX_WORKERS) as ex:
        while n <= max_pages and len(out) < len(want):
            nums = list(range(n, min(n + IDX_WORKERS, max_pages + 1)))
            if not nums:
                break
            htmls = list(ex.map(_safe_get,
                                [INDEX if i == 1 else PAGE.format(n=i) for i in nums]))
            oldest = None
            for html in htmls:
                if not html:
                    continue
                pages_read += 1
                for href, y, mo, d, slug, q, fy in LINK.findall(html):
                    date = f"{y}-{mo}-{d}"
                    if oldest is None or date < oldest:
                        oldest = date
                    tk = slug.split("-")[-1].upper()
                    if tk not in want:
                        continue
                    cur = out.get(tk)
                    if cur and cur["date"] >= date:
                        continue
                    out[tk] = {"url": "https://www.fool.com" + href, "date": date,
                               "quarter": f"Q{q} {fy}"}
            # 이 묶음에서 아무것도 못 읽었으면 더 가 봐야 소용없다
            if oldest is None:
                break
            if oldest < since:
                break
            n += IDX_WORKERS
    print(f"[컨콜] 목록 {pages_read}쪽 읽어 우리 종목 {len(out)}/{len(want)}건")
    return out


_DROP = re.compile(r"(?is)<(script|style|svg|noscript)[^>]*>.*?</\1>")


def body(url: str) -> str:
    """전문 본문만. 없으면 빈 문자열."""
    try:
        html = _get(url)
    except Exception as e:                         # noqa: BLE001
        print(f"[컨콜] 전문 실패({type(e).__name__}) {url[-60:]}")
        return ""
    i = html.find("article-body-transcript")
    if i < 0:
        i = html.find("article-body")
    if i < 0:
        return ""
    h = _DROP.sub(" ", html[i:])
    h = re.sub(r"(?is)<br\s*/?>|</p>|</h\d>|</div>", "\n", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = (h.replace("&#x27;", "'").replace("&amp;", "&")
          .replace("&quot;", '"').replace("&nbsp;", " "))
    h = re.sub(r"[ \t]+", " ", h)
    h = re.sub(r"\n{2,}", "\n", h).strip()
    # 본문 뒤에 붙는 추천 기사·결제 안내는 버린다
    for end in ("Read Next", "Premium Investing Services", "Motley Fool Returns"):
        j = h.find(end)
        if j > 1000:
            h = h[:j]
            break
    return h[:BODY_MAX]


def _ask(instruction: str) -> dict | None:
    """profile.py 와 같은 두 경로 — Claude Code 헤드리스, 실패하면 API."""
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") and shutil.which("claude"):
        try:
            r = subprocess.run([shutil.which("claude"), "-p", instruction,
                                "--model", MODEL],
                               capture_output=True, text=True, encoding="utf-8",
                               timeout=CLI_TIMEOUT)
            if r.returncode == 0:
                return _parse(r.stdout)
            print(f"[컨콜] Claude Code 오류(exit {r.returncode})")
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"[컨콜] Claude Code 호출 실패: {type(e).__name__}")
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            resp = anthropic.Anthropic().messages.create(
                model=MODEL, max_tokens=8000,
                messages=[{"role": "user", "content": instruction}])
            return _parse("".join(b.text for b in resp.content
                                  if getattr(b, "type", "") == "text"))
        except Exception as e:                     # noqa: BLE001
            print(f"[컨콜] API 실패: {type(e).__name__}")
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
    if not isinstance(d, dict):
        return None

    def rows(key, need):
        return [c for c in (d.get(key) or [])
                if isinstance(c, dict) and any(c.get(k) for k in need)]

    cm = rows("comments", ("head", "body"))
    qa = rows("qa", ("q", "a"))
    if not (cm or qa):
        return None
    intro = d.get("intro") if isinstance(d.get("intro"), dict) else {}
    return {"intro": {"what": str(intro.get("what") or ""),
                      "drivers": [str(x) for x in (intro.get("drivers") or []) if x]},
            "comments": cm, "qa": qa,
            "terms": rows("terms", ("t",)),
            "unverified": [str(x) for x in (d.get("unverified") or []) if x]}


def load(path: str) -> tuple[dict, dict]:
    """(이번 버전 캐시, 아직 다시 쓰지 못한 지난 버전 캐시).

    프롬프트를 바꾸면 전부 다시 써야 하는데, 한 실행에 열 건 남짓밖에 못
    쓴다(시간 예산). 그 사이 컨콜 탭이 비지 않게 지난 글을 깔아 두고,
    파일에도 prev 로 남긴다 — 안 남기면 다음 실행이 '버전이 맞다' 고 보고
    빈손으로 시작한다(profile.py 와 같은 구멍).
    """
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:                              # noqa: BLE001
        return {}, {}
    items, prev = d.get("items") or {}, d.get("prev") or {}
    if d.get("ver") != VER:
        print(f"[컨콜] 캐시 버전이 달라 다시 쓴다({d.get('ver')} != {VER}) — "
              f"다시 쓰기 전까지 지난 글 {len(items) + len(prev)}건을 쓴다")
        return {}, {**prev, **items}
    return items, prev


def save(path: str, items: dict, prev: dict | None = None) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        body = {"ver": VER, "items": items}
        left = {k: v for k, v in (prev or {}).items() if k not in items}
        if left:
            body["prev"] = left
        with open(path, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=1)
    except Exception as e:                         # noqa: BLE001
        print(f"[컨콜] 캐시 저장 실패: {type(e).__name__}")


def build(tickers, names, path: str, about: dict | None = None) -> dict:
    """{티커: {quarter, date, url, comments, qa}}.

    캐시 키는 '티커|분기' 다 — 같은 분기를 두 번 쓰지 않고, 다음 분기 콜이
    올라오면 그때 새로 쓴다.
    """
    want = {t.upper() for t in (tickers or [])}
    if not want:
        return {}
    cache, stale = load(path)
    about = about or {}
    # 실적 발표 카드가 보는 창과 같은 3주. 그보다 오래된 콜은 다음 분기가
    # 코앞이라 굳이 새로 쓰지 않는다(이미 쓴 것은 캐시에 남아 있다).
    since = (dt.date.today() - dt.timedelta(days=SINCE_DAYS)).isoformat()
    try:
        hit = index(want, since)
    except Exception as e:                         # noqa: BLE001
        print(f"::warning::[컨콜] 목록 수집 실패: {type(e).__name__}")
        hit = {}
    # 최근 콜부터 쓴다 — 예산에 걸려 다음 실행으로 넘어가는 것은 오래된 쪽이다
    todo = sorted(((t, v) for t, v in hit.items()
                   if f"{t}|{v['quarter']}" not in cache),
                  key=lambda tv: tv[1]["date"], reverse=True)
    print(f"::notice::[컨콜] 주도주·관심종목 {len(want)}종 중 전문 있는 것 "
          f"{len(hit)}건 · 새로 쓸 것 {len(todo)}건")

    deadline = time.monotonic() + BUDGET
    skipped = 0

    def one(item):
        t, v = item
        if time.monotonic() > deadline:
            return t, v, None
        text = body(v["url"])
        if len(text) < 2000:                       # 본문을 못 떼어낸 것이다
            print(f"[컨콜] {t} 본문 {len(text)}자 — 건너뜀")
            return t, v, None
        head = (f"[회사 개요]\n{about.get(t) or '(없음)'}\n\n"
                f"[{t}] {names.get(t, t)} — {v['quarter']} 어닝콜 전문\n\n")
        return t, v, _ask(SYSTEM + "\n\n" + head + text)

    done = 0
    if todo:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for t, v, d in ex.map(one, todo):
                if not d:
                    skipped += 1
                    continue
                cache[f"{t}|{v['quarter']}"] = {
                    "ticker": t, "quarter": v["quarter"], "date": v["date"],
                    "url": v["url"], **d}
                done += 1
        if done:
            save(path, cache, stale)
    print(f"[컨콜] 새로 쓴 콜 {done}건 (캐시 합계 {len(cache)})"
          + (f" — {skipped}건은 다음 실행으로" if skipped else ""))

    # 종목별로 가장 최근 분기 하나만 화면에 넘긴다
    out = {}
    for v in list(stale.values()) + list(cache.values()):   # 새 글이 덮어쓴다
        t = v.get("ticker")
        if t not in want:
            continue
        if t not in out or v.get("date", "") >= out[t].get("date", ""):
            out[t] = v
    return out
