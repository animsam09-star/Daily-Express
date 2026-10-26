# -*- coding: utf-8 -*-
"""concall 의 주소 파싱·본문 절단을 시험한다.

바깥 호출은 하지 않는다 — 러너 탐색에서 실측한 실제 모양을 재현한 조각으로
'슬러그에서 티커를 바로 뽑는가', '본문을 어디서 자르는가' 만 본다.
"""
import sys

sys.path.insert(0, ".")
import concall                                     # noqa: E402

IDX_HTML = """
<a href="/earnings/call-transcripts/2026/10/26/verizon-vz-q3-2026-earnings-call-transcript/">x</a>
<a href="/earnings/call-transcripts/2026/10/26/bfst-q3-2026-earnings-call-transcript/">x</a>
<a href="/earnings/call-transcripts/2026/10/26/coca-cola-femsa-kof-q3-2026-earnings-call-transcript/">x</a>
<a href="/earnings/call-transcripts/2026/10/20/verizon-vz-q2-2026-earnings-call-transcript/">옛 분기</a>
<a href="/investing/2026/10/26/some-other-article/">관계없는 글</a>
"""

TX_HTML = """<html><head><script>var junk="Read Next";</script></head><body>
<nav>Best ETFs to Buy Top Stocks</nav>
<div id="article-body-transcript" class="article-body transcript-content">
<h2>DATE</h2><p>Mon, Oct 26, 2026</p>
<h2>CALL PARTICIPANTS</h2><p>Chief Executive Officer - Dan Schulman</p>
<h2>TAKEAWAYS</h2><p>Postpaid Phone Net Additions -- 406,000 lines.</p>
<h2>Full Conference Call Transcript</h2>
<p>Dan Schulman: It&#x27;s Dan. {PAD}</p>
</div>
<h2>Read Next</h2><p>2 High-Yield Dividend Stocks</p>
</body></html>""".replace("{PAD}", "본문 " * 1200)


def check(name, got, want):
    ok = got == want
    print(("OK  " if ok else "FAIL") + f"  {name}")
    if not ok:
        print(f"      got={got!r}\n      want={want!r}")
    return ok


def main():
    rows = concall.LINK.findall(IDX_HTML)
    got = [(m[4].split("-")[-1].upper(), f"Q{m[5]} {m[6]}") for m in rows]
    ok = check("슬러그에서 티커·분기", got,
               [("VZ", "Q3 2026"), ("BFST", "Q3 2026"), ("KOF", "Q3 2026"),
                ("VZ", "Q2 2026")])

    # 같은 티커가 두 번 나오면 최신 날짜만 남고, 안 찾는 종목은 안 담는다
    saved, concall._safe_get = concall._safe_get, lambda u: IDX_HTML
    try:
        idx = concall.index({"VZ", "KOF", "NVDA"}, "2026-10-01", max_pages=1)
    finally:
        concall._safe_get = saved
    ok &= check("최신 분기만 남긴다", idx["VZ"]["quarter"], "Q3 2026")
    ok &= check("찾는 종목만 담는다", sorted(idx), ["KOF", "VZ"])

    # 목록이 찾는 기간보다 오래되면 더 읽지 않는다
    pages = []

    def paged(url):
        pages.append(url)
        return IDX_HTML

    saved, concall._safe_get = concall._safe_get, paged
    try:
        concall.index({"NVDA"}, "2026-11-01", max_pages=30)
    finally:
        concall._safe_get = saved
    ok &= check("오래된 쪽이 나오면 멈춘다(한 묶음만)", len(pages),
                concall.IDX_WORKERS)

    saved, concall._get = concall._get, lambda u: TX_HTML
    try:
        txt = concall.body("https://x")
    finally:
        concall._get = saved
    ok &= check("스크립트 속 'Read Next' 에 안 속는다", "var junk" in txt, False)
    ok &= check("추천 기사는 잘라낸다", "High-Yield Dividend" in txt, False)
    ok &= check("네비게이션은 안 들어온다", "Best ETFs" in txt, False)
    ok &= check("본문은 들어온다", "Postpaid Phone Net Additions" in txt, True)
    ok &= check("HTML 엔티티를 푼다", "It's Dan" in txt, True)

    print("\n통과" if ok else "\n실패")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
