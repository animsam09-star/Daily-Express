# -*- coding: utf-8 -*-
"""article-body 안이 실제로 무엇인지 본다(탐색용, 일회성)."""
import re
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
IDX = "https://www.fool.com/earnings-call-transcripts/"
LINK = re.compile(r'href="(/earnings/call-transcripts/\d{4}/\d{2}/\d{2}/'
                  r'([a-z0-9-]+?)-q\d-\d{4}-earnings-call-transcript/)"')


def note(s):
    print("::notice::[탐색3] " + str(s).replace("\n", " ")[:900])


def strip(html):
    h = re.sub(r"(?is)<(script|style|svg|noscript)[^>]*>.*?</\1>", " ", html)
    h = re.sub(r"(?is)<br\s*/?>|</p>|</h\d>", "\n", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = re.sub(r"&nbsp;?", " ", h)
    h = re.sub(r"[ \t]+", " ", h)
    return re.sub(r"\n{2,}", "\n", h).strip()


def main():
    r = requests.get(IDX, headers=UA, timeout=25)
    links = [m[0] for m in LINK.findall(r.text)]
    # 큰 종목이 섞인 쪽을 고른다 — VZ 처럼 아는 이름이 검증하기 좋다
    pick = next((u for u in links if "-vz-" in u or u.split("/")[-2].startswith("vz-")),
                links[0])
    h = requests.get("https://www.fool.com" + pick, headers=UA, timeout=25).text
    note(f"대상 {pick}")

    heads = re.findall(r"(?is)<h2[^>]*>(.*?)</h2>", h)
    note("h2: " + str([re.sub(r'<[^>]+>', '', x).strip()[:40] for x in heads][:14]))

    i = h.find("article-body")
    note(f"article-body 위치 {i} / html {len(h)}")
    if i > 0:
        seg = h[i:i + 400000]
        txt = strip(seg)
        note(f"article-body 이후 텍스트 {len(txt)}")
        note("머리 600: " + txt[:600])
        note("중간: " + txt[len(txt) // 2:len(txt) // 2 + 400])
        note("꼬리 400: " + txt[-400:])
    # 발언자 표기가 있는지 — 요약 품질이 여기서 갈린다
    full = strip(h)
    for mark in ("-- Analyst", "-- Chief", "President and", "Operator",
                 "Duration:", "Call Participants", "Contents:"):
        note(f"{mark!r} -> {len(re.findall(re.escape(mark), full))}회")
    return 0


if __name__ == "__main__":
    sys.exit(main())
