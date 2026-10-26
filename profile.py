# -*- coding: utf-8 -*-
"""기업개요를 한글로 다시 쓴다.

야후가 주는 개요는 영문 원문이고, 그걸 400자에서 자르면 문장 중간이
끊긴다 — 한글 브리핑 한가운데 영어 단락이 토막난 채 들어가 있었다.
Claude 로 한글 두세 문장으로 다시 쓴다.

개요는 매일 바뀌지 않는다. 그래서 캐시하고 새로 들어온 종목만 쓴다 —
종목 200개를 매일 다시 쓰면 토큰이 아깝고, 같은 회사 설명이 날마다
조금씩 달라지는 것도 좋지 않다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

MODEL = "claude-opus-5"
CLI_TIMEOUT = 300
BATCH = 10               # 한 번에 물어볼 종목 수(개요가 길어져 줄였다)
WORKERS = 4              # 묶음을 동시에 몇 개나 물을 것인가
# 개요 때문에 브리핑이 늦으면 안 된다. 첫 실행은 200종목이라 묶음이 열넷인데,
# 예산을 넘기면 거기서 멈추고 받은 것만 캐시한다 — 나머지는 다음 실행이
# 이어서 쓴다. 캐시가 차면 이 단계는 곧 0초가 된다.
BUDGET = 240             # 초
VER = 2                  # 프롬프트를 바꾸면 올린다 — 캐시를 통째로 새로 만든다
RAW_MAX = 2200           # 원문을 이만큼만 넣는다(야후 원문이 2천 자를 넘는다)

SYSTEM = """\
너는 증권사 리서치 어시스턴트다. 아래 기업들의 개요를 한국어로 다시 쓴다.
읽는 사람은 이 회사를 처음 보는 투자자다. "무엇을 파는 회사이고 그 돈이
어디서 나오는가" 가 읽고 나서 머리에 남아야 한다.

규칙
1. 종목마다 네댓 문장. 다음 순서로 쓴다.
   (1) 무엇을 파는 회사인가 — 업종 이름이 아니라 제품·서비스 이름으로.
       원문에 제품명·브랜드·모델명이 있으면 그대로 적는다.
   (2) 사업부문이 원문에 나뉘어 있으면 부문 이름과 각 부문이 파는 것.
   (3) 누구에게 파는가 — 고객군·전방 산업·판매 경로(직판/대리점/통신사 등).
   (4) 지역, 설립 연도, 본사. 원문에 있으면 마지막에 한 문장으로.
2. 원문에 없는 사실을 보태지 않는다. 특히 매출 비중·점유율처럼 숫자로
   보이는 것은 원문에 그 숫자가 있을 때만 쓴다.
3. 실적 수치·목표주가·전망·투자의견은 쓰지 않는다 — 다른 화면이 맡는다.
4. 마케팅 문구를 걷어낸다: '선도적', '혁신적', '세계 최고', '글로벌 리더',
   '토털 솔루션'. 원문이 그렇게 쓰여 있어도 사실만 남긴다.
5. 영문 고유명사는 그대로 두되(제품명·부문명), 설명은 한국어로 푼다.
6. 평서문 종결(-한다/-이다). 종목명을 문장마다 반복하지 않는다.

JSON 만 출력한다. 설명도 코드펜스도 없이 객체 하나만.
형식: {"items": [{"ticker": "NVDA", "text": "개요"}]}
"""


def _ask(instruction: str) -> dict | None:
    """Claude Code 헤드리스 → 실패하면 API. news.py 와 같은 두 경로다."""
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") and shutil.which("claude"):
        try:
            r = subprocess.run([shutil.which("claude"), "-p", instruction,
                                "--model", MODEL],
                               capture_output=True, text=True, encoding="utf-8",
                               timeout=CLI_TIMEOUT)
            if r.returncode == 0:
                return _parse(r.stdout)
            print(f"[개요] Claude Code 오류(exit {r.returncode})")
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"[개요] Claude Code 호출 실패: {type(e).__name__}")
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            resp = anthropic.Anthropic().messages.create(
                model=MODEL, max_tokens=8000,
                messages=[{"role": "user", "content": instruction}])
            return _parse("".join(b.text for b in resp.content
                                  if getattr(b, "type", "") == "text"))
        except Exception as e:                     # noqa: BLE001
            print(f"[개요] API 실패: {type(e).__name__}")
    return None


def _parse(out: str) -> dict | None:
    """모델이 앞뒤에 말을 붙여도 JSON 객체만 집어낸다."""
    s = (out or "").strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(s[i:j + 1])
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def load(path: str) -> tuple[dict, dict]:
    """(이번 버전 캐시, 지난 버전 캐시).

    프롬프트를 고치면 버전이 올라가 캐시를 다시 쓰게 되는데, 200종목을
    한 실행에 다 쓰지는 못한다(시간 예산). 그 사이 개요가 통째로 비면
    화면이 영문 원문으로 되돌아가므로, 지난 버전 글을 임시로 깔아 둔다.
    """
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:                              # noqa: BLE001
        return {}, {}
    items = d.get("items") or {}
    if d.get("ver") != VER:
        print(f"[개요] 캐시 버전이 달라 다시 쓴다({d.get('ver')} != {VER}) — "
              f"다시 쓰기 전까지는 지난 글 {len(items)}종목을 쓴다")
        return {}, items
    return items, {}


def save(path: str, items: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ver": VER, "items": items}, f, ensure_ascii=False, indent=1)
    except Exception as e:                         # noqa: BLE001
        print(f"[개요] 캐시 저장 실패: {type(e).__name__}")


def build(profiles: dict, path: str) -> dict:
    """{티커: 한글 개요}. profiles 는 {티커: {"summary_raw"|"summary": 원문}}.

    캐시에 있는 종목은 건너뛴다. 한 번에 BATCH 개씩 묶어 묻고, 한 묶음이
    실패해도 나머지는 그대로 간다.
    """
    cache, stale = load(path)
    todo = [(t, (p.get("summary_raw") or p.get("summary") or "").strip())
            for t, p in (profiles or {}).items()
            if t not in cache and (p.get("summary_raw") or p.get("summary"))]
    if not todo:
        print(f"[개요] 캐시로 전부 해결({len(cache)}종목)")
        return cache

    chunks = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    deadline = time.monotonic() + BUDGET

    def one(chunk):
        if time.monotonic() > deadline:
            return None                            # 예산을 넘겼으면 묻지 않는다
        body = "\n\n".join(f"[{t}]\n{raw[:RAW_MAX]}" for t, raw in chunk)
        return _ask(SYSTEM + "\n\n" + body)

    done, skipped = 0, 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for d in ex.map(one, chunks):
            if d is None:
                skipped += 1
                continue
            for it in d.get("items") or []:
                tk = (it.get("ticker") or "").strip()
                text = (it.get("text") or "").strip()
                if tk and text:
                    cache[tk] = text
                    done += 1
    print(f"[개요] 새로 쓴 종목 {done}/{len(todo)} (캐시 합계 {len(cache)})"
          + (f" — 예산 {BUDGET}초를 넘겨 {skipped}묶음은 다음 실행으로" if skipped else ""))
    if done:
        save(path, cache)
    # 아직 못 쓴 종목은 지난 버전 글로 메운다(캐시 파일에는 남기지 않는다 —
    # 남기면 버전이 올라간 셈이 되어 영영 다시 안 쓴다).
    return {**{k: v for k, v in stale.items() if k not in cache}, **cache}
