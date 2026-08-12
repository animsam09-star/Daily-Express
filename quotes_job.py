"""배포된 대시보드의 시세만 한 시간마다 새로 받아 quotes.json 으로 남긴다.

브리핑 전체를 매시간 다시 도는 것은 못 한다 — 300종목 2년 캔들을 매시간
당기면 야후가 막고, 뉴스 요약 비용도 시간마다 곱해진다. 장중에 실제로
바뀌는 값은 현재가·등락률·시가총액뿐이므로 그것만 받는다.

대상 종목은 배포된 페이지 자신에게서 읽는다(페이지에 심긴 JSON). 별도의
목록 파일을 두면 아침 빌드와 어긋날 수 있는데, 페이지를 읽으면 화면에
실제로 떠 있는 종목과 항상 같다.

    python quotes_job.py --out quotes.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys

import requests

import kr_sources
import sources
from sources import TIMEOUT, UA, VERIFY

BASE = "https://daily-express-animsam09-star.pages.dev"
PAGES = {"us": "us.html", "kr": "kr.html"}
# 페이지에 심긴 데이터 블록. webgen 이 <script id="data"> 로 넣는다.
DATA_RE = re.compile(
    r'<script id="data" type="application/json">(.*?)</script>', re.S)
KST = dt.timezone(dt.timedelta(hours=9))


def fetch_payload(url: str) -> dict | None:
    """배포된 페이지에서 데이터 블록만 떼어낸다."""
    r = requests.get(url, headers=UA, verify=VERIFY, timeout=TIMEOUT)
    r.raise_for_status()
    m = DATA_RE.search(r.text)
    if not m:
        return None
    # webgen 이 </ 를 <\/ 로 escape 해 넣는다 — 되돌려야 JSON 이 된다
    return json.loads(m.group(1).replace("<\\/", "</"))


def targets(payload: dict) -> tuple[list[str], dict[str, str]]:
    """(야후 심볼 목록, {야후 심볼: 페이지에서 쓰는 키}).

    한국 종목은 페이지에 6자리 코드로 실려 있어 .KS/.KQ 를 붙여야 하고,
    돌려줄 때는 다시 6자리로 되돌려야 페이지가 알아본다.
    """
    keys: dict[str, str] = {}
    kr = payload.get("market") == "kr"

    def add(sym, key=None):
        if sym:
            keys[sym] = sym if key is None else key

    for s in payload.get("summary") or []:
        add(s.get("sym"))               # 지수·환율. 금리 카드는 sym 이 비어 있다

    codes = set()
    for sec in payload.get("sectors") or []:
        if not kr:
            add(sec.get("symbol"))      # 미국 섹터 등락률은 SPDR ETF 자체
        codes |= {h.get("ticker") for h in (sec.get("holdings") or [])}
        codes |= {c for c, _ in (sec.get("index") or [])}
    codes = sorted(c for c in codes if c)

    if kr:
        # 페이지에는 6자리 코드로 실려 있다. 조회는 야후 심볼로 하고,
        # 돌려줄 때는 다시 6자리로 되돌려야 페이지가 알아본다.
        for code, sym in resolve_kr(codes).items():
            add(sym, code)
    else:
        for c in codes:
            add(c)
    return sorted(keys), keys


def resolve_kr(codes: list[str]) -> dict[str, str]:
    """{6자리 코드: 야후 심볼}. 가리지 못한 종목은 아예 빠진다.

    주가로 가리면 안 된다 — 야후는 아무 6자리 코드에나 .KS 와 .KQ 둘 다
    가격을 돌려주는데, 상장 시장이 아닌 쪽은 남의 회사 값이다(실측:
    주성엔지니어링 036930 은 .KS 31,550 / .KQ 174,000 이고 네이버 현재가는
    174,000). 진짜 상장 시장을 가리는 단서는 시가총액뿐이라, 그것으로
    판별하는 kr_sources 의 검증된 함수를 그대로 쓴다.

    시가총액이 양쪽 다 안 오면 확신할 수 없으므로 그 종목은 갱신하지
    않는다 — 틀린 값으로 덮느니 아침 값을 그대로 두는 편이 낫다.
    """
    quotes = kr_sources._resolve_suffixes(codes)
    out = {}
    for c in codes:
        sym = kr_sources._ys(c)
        if (quotes.get(sym) or {}).get("market_cap"):
            out[c] = sym
    skipped = len(codes) - len(out)
    if skipped:
        print(f"[kr] 상장 시장을 못 가린 {skipped}종목은 갱신에서 뺀다",
              file=sys.stderr)
    return out


def collect(market: str) -> dict | None:
    url = f"{BASE}/{PAGES[market]}"
    try:
        payload = fetch_payload(url)
    except Exception as e:                             # noqa: BLE001
        print(f"[{market}] 페이지 회수 실패: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    if not payload:
        print(f"[{market}] 페이지에서 데이터 블록을 못 찾음", file=sys.stderr)
        return None

    syms, keys = targets(payload)
    if not syms:
        print(f"[{market}] 조회할 종목이 없음", file=sys.stderr)
        return None
    quotes = sources.fetch_quotes(syms)

    out = {}
    for sym, q in quotes.items():
        if not q or q.get("price") is None:
            continue
        row = {"price": q["price"]}
        if q.get("chg_pct") is not None:
            row["chg_pct"] = round(q["chg_pct"], 2)
        if q.get("market_cap"):
            row["market_cap"] = q["market_cap"]
        # 환율 카드는 % 가 아니라 원 차이로 표기한다
        if sym == "KRW=X" and q.get("chg_pct") is not None and q.get("price"):
            prev = q["price"] / (1 + q["chg_pct"] / 100)
            row["chg"] = round(q["price"] - prev, 1)
        out[keys.get(sym, sym)] = row

    print(f"[{market}] {len(out)}/{len(syms)}종목 시세")
    if len(out) < len(syms) * 0.5:
        # 절반도 못 받았으면 야후가 막힌 것이다. 반쪽짜리로 덮어쓰면 화면의
        # 숫자가 뒤죽박죽 섞이므로 이번 시간은 통째로 건너뛴다.
        print(f"[{market}] 절반 미만 — 이번 갱신 생략", file=sys.stderr)
        return None
    return {"at": dt.datetime.now(KST).strftime("%m.%d %H:%M"), "quotes": out}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="quotes.json")
    ap.add_argument("--markets", default="us,kr")
    args = ap.parse_args()

    try:
        with open(args.out, encoding="utf-8") as fh:
            prev = json.load(fh)
    except (OSError, json.JSONDecodeError):
        prev = {}

    out = dict(prev)
    ok = 0
    for market in args.markets.split(","):
        market = market.strip()
        if market not in PAGES:
            continue
        got = collect(market)
        if got:                       # 실패한 시장은 직전 값을 그대로 둔다
            out[market] = got
            ok += 1

    if not ok:
        print("갱신된 시장이 없습니다 — 파일을 건드리지 않습니다", file=sys.stderr)
        return 1
    out["updated"] = dt.datetime.now(KST).isoformat(timespec="seconds")
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"{args.out} 기록 — {ok}개 시장")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
