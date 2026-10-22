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

MODEL = "claude-opus-5"
CLI_TIMEOUT = 300
BATCH = 15               # 한 번에 물어볼 종목 수
VER = 1                  # 프롬프트를 바꾸면 올린다 — 캐시를 통째로 새로 만든다
RAW_MAX = 1500           # 원문을 이만큼만 넣는다(야후 원문은 2천 자에 이른다)

SYSTEM = """\
너는 증권사 리서치 어시스턴트다. 아래 기업들의 개요를 한국어로 다시 쓴다.

규칙
1. 종목마다 두세 문장. 첫 문장에 '무엇을 파는 회사인지' 를 쓴다. 업종
   이름만 대지 말고 제품·서비스를 구체적으로 적는다.
2. 매출 구성·주력 부문이 원문에 있으면 이어서 적는다. 없으면 지어내지 않는다.
3. 원문에 없는 사실을 보태지 않는다. 실적 수치·점유율·목표주가·전망은 쓰지
   않는다 — 그건 다른 화면이 맡는다.
4. 마케팅 문구를 걷어낸다: '선도적', '혁신적', '세계 최고', '글로벌 리더'.
5. 평서문 종결(-한다/-이다). 종목명을 문장 앞에 반복하지 않는다.

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
                model=MODEL, max_tokens=4000,
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


def load(path: str) -> dict:
    """캐시. 버전이 다르면 버린다(프롬프트가 바뀐 것이다)."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:                              # noqa: BLE001
        return {}
    if d.get("ver") != VER:
        print(f"[개요] 캐시 버전이 달라 버린다({d.get('ver')} != {VER})")
        return {}
    return d.get("items") or {}


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
    cache = load(path)
    todo = [(t, (p.get("summary_raw") or p.get("summary") or "").strip())
            for t, p in (profiles or {}).items()
            if t not in cache and (p.get("summary_raw") or p.get("summary"))]
    if not todo:
        print(f"[개요] 캐시로 전부 해결({len(cache)}종목)")
        return cache

    done = 0
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        body = "\n\n".join(f"[{t}]\n{raw[:RAW_MAX]}" for t, raw in chunk)
        d = _ask(SYSTEM + "\n\n" + body)
        for it in (d or {}).get("items") or []:
            tk, text = (it.get("ticker") or "").strip(), (it.get("text") or "").strip()
            if tk and text:
                cache[tk] = text
                done += 1
    print(f"[개요] 새로 쓴 종목 {done}/{len(todo)} (캐시 합계 {len(cache)})")
    if done:
        save(path, cache)
    return cache
