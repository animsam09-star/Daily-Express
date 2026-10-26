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
WORKERS = 3
BUDGET = 240             # 초. 넘기면 받은 것만 캐시하고 다음 실행으로 넘긴다
                         # (개요와 같은 예산 — 06:05 브리핑이 06:30 까지 나가야 한다)
VER = 1                  # 프롬프트를 바꾸면 올린다
BODY_MAX = 60000         # 전문 글자 상한(사이트가 길어져도 요청이 터지지 않게)
SINCE_DAYS = 21          # 며칠 전 콜까지 찾을 것인가(실적 발표 카드와 같은 창)

SYSTEM = """\
너는 증권사 애널리스트다. 아래 어닝콜 전문을 읽고 한국어 메모로 요약한다.
읽는 사람은 콜을 듣지 못한 투자자다. 숫자와 그 숫자가 왜 그런지가 남아야 한다.

[주요 코멘트] 6~8개
- 각 항목은 '제목'과 '본문' 으로 나눈다.
- 제목은 결론과 핵심 수치를 같이 적는다.
  예: "SI 부문 최대 성장동력 — 매출 $7.6B"
  예: "Compute 병목은 GPU보다 Power·Cooling — 2027년 최소 13GW 목표"
- 본문은 한두 문장. 증감률·비교 시점·가이던스를 전문에 있는 대로 적는다.
- 전문에 없는 숫자·전망을 지어내지 않는다. 회사 표현이 모호하면 모호한 대로 적는다.

[주요 Q&A] 5~8개
- 애널리스트 질문을 한 문장으로 줄이고(Q), 경영진 답변의 알맹이를 적는다(A).
- 인사말·감사 표현은 버린다. 답을 피한 질문은 피했다고 적는다.
- A 에도 숫자가 있으면 반드시 남긴다.

공통
- 평서문 종결(-한다/-이다). 영문 고유명사·부문명·제품명은 그대로 둔다.
- 금액 단위는 전문 표기를 그대로 쓴다($14.6B 등).
- 마케팅 수사('선도적','혁신적')는 걷어낸다.

JSON 만 출력한다. 설명도 코드펜스도 없이 객체 하나만.
형식: {"comments":[{"head":"제목","body":"본문"}],"qa":[{"q":"질문","a":"답변"}]}
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
    cm = [c for c in (d.get("comments") or [])
          if isinstance(c, dict) and (c.get("head") or c.get("body"))]
    qa = [c for c in (d.get("qa") or [])
          if isinstance(c, dict) and (c.get("q") or c.get("a"))]
    return {"comments": cm, "qa": qa} if (cm or qa) else None


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:                              # noqa: BLE001
        return {}
    if d.get("ver") != VER:
        print(f"[컨콜] 캐시 버전이 달라 버린다({d.get('ver')} != {VER})")
        return {}
    return d.get("items") or {}


def save(path: str, items: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ver": VER, "items": items}, f, ensure_ascii=False, indent=1)
    except Exception as e:                         # noqa: BLE001
        print(f"[컨콜] 캐시 저장 실패: {type(e).__name__}")


def build(tickers, names, path: str) -> dict:
    """{티커: {quarter, date, url, comments, qa}}.

    캐시 키는 '티커|분기' 다 — 같은 분기를 두 번 쓰지 않고, 다음 분기 콜이
    올라오면 그때 새로 쓴다.
    """
    want = {t.upper() for t in (tickers or [])}
    if not want:
        return {}
    cache = load(path)
    # 실적 발표 카드가 보는 창과 같은 3주. 그보다 오래된 콜은 다음 분기가
    # 코앞이라 굳이 새로 쓰지 않는다(이미 쓴 것은 캐시에 남아 있다).
    since = (dt.date.today() - dt.timedelta(days=SINCE_DAYS)).isoformat()
    try:
        hit = index(want, since)
    except Exception as e:                         # noqa: BLE001
        print(f"::warning::[컨콜] 목록 수집 실패: {type(e).__name__}")
        hit = {}
    todo = [(t, v) for t, v in hit.items() if f"{t}|{v['quarter']}" not in cache]
    print(f"[컨콜] 전문 있는 종목 {len(hit)}건 · 새로 쓸 것 {len(todo)}건")

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
        head = (f"[{t}] {names.get(t, t)} — {v['quarter']} 어닝콜 전문\n\n")
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
            save(path, cache)
    print(f"[컨콜] 새로 쓴 콜 {done}건 (캐시 합계 {len(cache)})"
          + (f" — {skipped}건은 다음 실행으로" if skipped else ""))

    # 종목별로 가장 최근 분기 하나만 화면에 넘긴다
    out = {}
    for v in cache.values():
        t = v.get("ticker")
        if t not in want:
            continue
        if t not in out or v.get("date", "") > out[t].get("date", ""):
            out[t] = v
    return out
