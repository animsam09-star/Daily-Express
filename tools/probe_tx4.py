# -*- coding: utf-8 -*-
"""종목 페이지에서 그 종목의 전문으로 바로 갈 수 있는지 본다(탐색용).

날짜 목록만 보면 최근 2~3일치밖에 안 잡힌다(실측: 우리 종목 34건이 발표했는데
3건만 찾았다). 종목별 페이지에 그 회사 전문 링크가 있으면 티커로 바로 간다.
"""
import re
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
TX = re.compile(r'href="(/earnings/call-transcripts/[^"]+)"')


def note(s):
    print("::notice::[탐색4] " + str(s).replace("\n", " ")[:900])


def main():
    # 이번 실행에서 발표했는데 못 찾은 종목들로 시험한다
    for ex, tk in (("nyse", "vz"), ("nyse", "nue"), ("nyse", "axp"),
                   ("nasdaq", "cmcsa"), ("nyse", "slb")):
        for url in (f"https://www.fool.com/quote/{ex}/{tk}/",
                    f"https://www.fool.com/quote/{ex}/{tk}/earnings-call-transcripts/"):
            try:
                r = requests.get(url, headers=UA, timeout=25)
                links = sorted(set(TX.findall(r.text)))
            except Exception as e:                 # noqa: BLE001
                note(f"{tk} {url[-40:]} !{type(e).__name__}")
                continue
            note(f"{tk.upper()} {url.split('fool.com')[1]} HTTP {r.status_code} "
                 f"len {len(r.text)} 전문링크 {len(links)}")
            if links:
                note(f"  예: {links[:3]}")
                break
    return 0


if __name__ == "__main__":
    sys.exit(main())
