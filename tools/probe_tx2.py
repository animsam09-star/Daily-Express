# -*- coding: utf-8 -*-
"""전문 페이지에서 본문만 어떻게 떼어낼지 실측한다(탐색용, 일회성).

1차 탐색에서 목록·전문 모두 HTTP 200 인 것은 확인했다. 다만 태그를 전부
벗기면 40만 자가 나오는데 대부분 사이트 네비게이션이다 — 본문 경계를
어디서 잡을지 눈으로 봐야 한다.
"""
import re
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
IDX = "https://www.fool.com/earnings-call-transcripts/"
LINK = re.compile(r'href="(/earnings/call-transcripts/'
                  r'(\d{4})/(\d{2})/(\d{2})/([a-z0-9-]+?)'
                  r'-q(\d)-(\d{4})-earnings-call-transcript/)"')


def note(s):
    print("::notice::[탐색2] " + str(s).replace("\n", " ")[:900])


def text_of(html):
    h = re.sub(r"(?is)<(script|style|svg|noscript)[^>]*>.*?</\1>", " ", html)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", h).strip()


def main():
    r = requests.get(IDX, headers=UA, timeout=25)
    hits = LINK.findall(r.text)
    # 슬러그의 'q{N}' 바로 앞 토큰이 티커다: coca-cola-femsa-kof-q3-... -> KOF
    rows = [(m[4].split("-")[-1].upper(), m[0], f"{m[1]}-{m[2]}-{m[3]}",
             f"Q{m[5]} {m[6]}") for m in hits]
    seen, uniq = set(), []
    for t in rows:
        if t[1] in seen:
            continue
        seen.add(t[1])
        uniq.append(t)
    note(f"목록 {len(uniq)}건 · 티커 {[u[0] for u in uniq[:14]]}")

    u = "https://www.fool.com" + uniq[0][1]
    h = requests.get(u, headers=UA, timeout=25).text
    note(f"대상 {uniq[0][0]} {uniq[0][2]} {uniq[0][3]} html {len(h)}")

    # 본문 컨테이너 후보
    for pat in (r'<div[^>]+class="[^"]*article-body[^"]*"',
                r'id="article-body"', r'<article[^>]*>',
                r'data-id="article-body"', r'class="[^"]*tailwind-article-body'):
        note(f"후보 {pat} -> {len(re.findall(pat, h))}개")

    full = text_of(h)
    note(f"전체 텍스트 {len(full)}")
    for mark in ("Prepared Remarks", "Questions & Answers", "Questions and Answers",
                 "Operator", "Call participants", "This article is a transcript",
                 "Duration:", "More ", "Motley Fool Transcribing"):
        note(f"표식 {mark!r} -> {[m.start() for m in re.finditer(re.escape(mark), full)][:6]}")

    i = full.find("Prepared Remarks")
    if i > 0:
        note("본문 시작부: " + full[i:i + 420])
        j = full.find("Call participants", i)
        note(f"Call participants 위치 {j} / 추정 본문 길이 {max(0, j - i)}")
        if j > 0:
            note("본문 끝부: " + full[max(i, j - 400):j + 160])
    return 0


if __name__ == "__main__":
    sys.exit(main())
