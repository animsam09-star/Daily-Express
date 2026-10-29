# -*- coding: utf-8 -*-
"""어닝콜 전문을 어디서 받을 수 있는지 러너에서 실측한다.

이 컨테이너(세션)는 바깥 호스트가 막혀 있어 주소를 지어내는 수밖에 없는데,
그러다 틀린 주소를 코드에 박은 적이 있다. 그래서 수집 코드를 쓰기 전에
Actions 러너에서 한 번 찔러 보고 결과만 ::notice:: 로 받는다.

결과는 주소·상태·길이·모양뿐이다. 시크릿은 찍지 않는다.
"""
import re
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}
T = 25


def get(url, **kw):
    try:
        r = requests.get(url, headers=UA, timeout=T, **kw)
        return r.status_code, r.text
    except Exception as e:                         # noqa: BLE001
        return 0, f"!{type(e).__name__}"


def note(s):
    print("::notice::[탐색] " + s.replace("\n", " ")[:900])


def main():
    # 1) 모틀리풀 전문 목록 — 티커를 역산할 수 없으니 목록에서 찾아야 한다
    code, html = get("https://www.fool.com/earnings-call-transcripts/")
    note(f"fool 목록 HTTP {code} len {len(html)}")
    if code == 200:
        hrefs = re.findall(r'href="(/earnings/call-transcripts/[^"]+)"', html)
        note(f"fool 목록 전문링크 {len(hrefs)}개, 예: {hrefs[:3]}")
        # 목록에 티커가 같이 적혀 있는지 — 있으면 종목을 바로 골라낼 수 있다
        tick = re.findall(r'\(([A-Z]{1,5})\s*[-–]?\s*[\d.]*%?\)', html)
        note(f"fool 목록 티커후보 {len(set(tick))}종: {sorted(set(tick))[:12]}")
        if hrefs:
            u = "https://www.fool.com" + hrefs[0]
            c2, h2 = get(u)
            body = re.sub(r"<[^>]+>", " ", h2)
            body = re.sub(r"\s+", " ", body)
            note(f"fool 전문 HTTP {c2} html {len(h2)} 본문글자 {len(body)} "
                 f"url {u[:110]}")
            note("fool 전문 맛보기: " + body[2000:2260])

    # 2) 키 없이 쓸 수 있다고 알려진 후보들
    for name, url in (
        ("discountingcashflows",
         "https://discountingcashflows.com/api/transcript/?ticker=AAPL"),
        ("roic(키없음)", "https://api.roic.ai/v1/rql/earnings-call?ticker=AAPL"),
        ("fool quote", "https://www.fool.com/quote/nasdaq/nvda/"),
    ):
        c, t = get(url)
        note(f"{name} HTTP {c} len {len(t)} head {t[:120]!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
