"""크게 움직인 종목의 관련 뉴스 헤드라인 수집 + Claude 한국어 요약.

주가가 왜 움직였는지는 사후 해석이라 자동으로 단정하면 틀린 설명이 나간다.
그래서 이 모듈은 '이유'를 만들어내지 않는다. 실제 수집한 헤드라인만 근거로 삼고,
헤드라인이 등락을 설명하지 못하면 그렇다고 답하게 한다.

ANTHROPIC_API_KEY 가 없으면 요약을 건너뛰고 헤드라인만 남긴다(발송은 계속된다).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

import requests
import urllib3

from sources import TIMEOUT, UA, VERIFY

if not VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MOVE_THRESHOLD = 3.0        # 이 이상 움직인 종목만 뉴스를 찾는다
HEADLINES_PER_TICKER = 4
MODEL = "claude-opus-5"
CLI_TIMEOUT = 180           # Claude Code 헤드리스 호출 상한(초)

SEARCH_URL = (
    "https://query1.finance.yahoo.com/v1/finance/search"
    "?q={q}&newsCount={n}&quotesCount=0"
)

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
                        "description": "한국어 한 줄 요약(40자 내외). 헤드라인이 등락을 "
                                       "설명하지 못하면 빈 문자열.",
                    },
                },
                "required": ["ticker", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

SYSTEM = """너는 한국 증권사 리서치 어시스턴트다. 미국 주식의 당일 등락과 관련 뉴스 헤드라인을 받아,
각 종목에 대해 한국어 한 줄 메모를 만든다.

지켜야 할 규칙:
1. 반드시 제공된 헤드라인에만 근거한다. 헤드라인에 없는 사실, 수치, 배경을 지어내지 않는다.
2. 헤드라인이 그 종목의 등락과 무관하거나 설명이 안 되면 note 를 빈 문자열로 둔다.
   억지로 채우지 않는 것이 맞는 답이다.
3. 인과를 단정하지 않는다. "~때문에 급락"이 아니라 "~보도" 처럼 사실만 적는다.
4. 40자 내외로 짧게. 종목명은 반복하지 말고 내용만 적는다.
5. 등락 방향과 헤드라인 내용이 어긋나면(악재인데 상승 등) 그대로 두고 단정하지 않는다."""


def _headlines(ticker: str, name: str) -> list[str]:
    """야후 뉴스 검색. 티커는 노이즈가 많아 사명으로도 한 번 더 찾는다."""
    out, seen = [], set()
    for q in (ticker, name.split()[0]):
        try:
            r = requests.get(
                SEARCH_URL.format(q=requests.utils.quote(q), n=HEADLINES_PER_TICKER),
                headers=UA, verify=VERIFY, timeout=TIMEOUT,
            )
            r.raise_for_status()
            for n in r.json().get("news", []):
                t = (n.get("title") or "").strip()
                if t and t not in seen:
                    seen.add(t)
                    out.append(t)
        except Exception:                      # noqa: BLE001
            continue
        if len(out) >= HEADLINES_PER_TICKER:
            break
    return out[:HEADLINES_PER_TICKER]


def collect_movers(holdings: dict) -> list[dict]:
    """섹터 보유종목 중 크게 움직인 것만 골라 헤드라인을 붙인다."""
    movers, seen = [], set()
    for hs in holdings.values():
        for h in hs:
            chg = h.get("chg_pct")
            if chg is None or abs(chg) < MOVE_THRESHOLD or h["ticker"] in seen:
                continue
            seen.add(h["ticker"])
            movers.append({"ticker": h["ticker"], "name": h["name"], "chg_pct": chg})

    if not movers:
        return []
    with ThreadPoolExecutor(max_workers=8) as ex:
        heads = ex.map(lambda m: _headlines(m["ticker"], m["name"]), movers)
    for m, hl in zip(movers, heads):
        m["headlines"] = hl
    return [m for m in movers if m["headlines"]]


def _prompt(movers: list[dict]) -> str:
    lines = []
    for m in movers:
        lines.append(f"[{m['ticker']}] {m['name']} {m['chg_pct']:+.2f}%")
        lines.extend(f"  - {h}" for h in m["headlines"])
    return "\n".join(lines)


def _parse(text: str) -> dict[str, str]:
    """모델 출력에서 {티커: 메모}. 앞뒤에 잡텍스트가 붙어도 JSON 본문만 건진다."""
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
    return {i["ticker"]: i["note"].strip()
            for i in data.get("items", [])
            if i.get("ticker") and i.get("note", "").strip()}


def _via_claude_code(movers: list[dict]) -> dict[str, str] | None:
    """Claude Code 헤드리스 모드. Pro/Max 구독 토큰으로 동작해 API 크레딧이 들지 않는다.

    `claude setup-token` 으로 만든 CLAUDE_CODE_OAUTH_TOKEN 을 쓴다.
    쓸 수 없는 상황이면 None 을 돌려 API 키 경로로 넘긴다.
    """
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return None
    exe = shutil.which("claude")
    if not exe:
        print("[news] CLAUDE_CODE_OAUTH_TOKEN 은 있으나 claude CLI 가 없음")
        return None

    instruction = (
        SYSTEM
        + "\n\n아래 입력에 대해 JSON 만 출력한다. 설명·코드펜스 없이 JSON 객체 하나만.\n"
          '형식: {"items": [{"ticker": "TICKER", "note": "한 줄 메모"}]}\n\n'
        + _prompt(movers)
    )
    try:
        r = subprocess.run(
            [exe, "-p", instruction, "--model", MODEL],
            capture_output=True, text=True, encoding="utf-8", timeout=CLI_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[news] Claude Code 호출 실패: {type(e).__name__}: {e}")
        return None
    if r.returncode != 0:
        print(f"[news] Claude Code 오류(exit {r.returncode}): {(r.stderr or '')[:300]}")
        return None
    return _parse(r.stdout)


def _via_api(movers: list[dict]) -> dict[str, str]:
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
            model=MODEL,
            max_tokens=8000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA},
            },
            messages=[{"role": "user", "content": _prompt(movers)}],
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


def summarize(movers: list[dict]) -> dict[str, str]:
    """구독 토큰을 먼저 쓰고, 없으면 API 키로 넘어간다. 둘 다 없으면 조용히 생략."""
    if not movers:
        return {}
    via_cc = _via_claude_code(movers)
    if via_cc is not None:
        return via_cc
    return _via_api(movers)


def build(holdings: dict) -> dict[str, str]:
    """섹터 보유종목 → {티커: 한 줄 메모}. 어느 단계가 실패해도 발송은 막지 않는다."""
    try:
        movers = collect_movers(holdings)
    except Exception as e:                     # noqa: BLE001
        print(f"[news] 헤드라인 수집 실패: {type(e).__name__}: {e}")
        return {}
    if not movers:
        return {}
    print(f"[news] 급등락 {len(movers)}종목 헤드라인 수집 — 요약 요청")
    return summarize(movers)
